# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""A CATEGORIA da pasta criada manda no vínculo (21/10/2026, decisões do dono).

A pasta que a equipe criou no Tuta só liga com a categoria que uma PESSOA
escolheu (a regra de palavras só sugere — nunca Chamado):
  • sem categoria → o e-mail NOVO fica guardado sem ligar (`ignorado`/
    `pasta_sem_categoria`, nada em claro) e é relido quando alguém escolher
    (só os dos últimos `RELER_DIAS` dias, e depois do que acabou de chegar);
    a resposta num fio ou com o protocolo do site que a equipe já atende liga
    como sempre (a prova coluna a coluna: `test_mail_pasta_como_hoje.py`);
  • `site` ("Site (como hoje)", decisão 9 do dono) → liga exatamente como
    antes (o chamado RF6 do site pelo protocolo; a 0424 a dá às pastas de site
    da Uranyx e da Charlots; a prova coluna a coluna:
    `test_mail_pasta_como_hoje.py`);
  • `venda` → a conversa de E-MAIL daquela venda, achada pelo nº (criada se
    não existe — também quando o nº ainda não chegou ao Bling, com o alerta
    `venda_pelo_numero`); nunca um caso, nunca a conversa de chat;
  • `informativo` → uma LINHA do item da pasta na aba E-mail (sem conversa);
  • `chamado` → a Pós-venda › Chamados (`posvenda.ligar_ao_chamado`; o
    contrato está em `scratchpad/emails/contrato_posvenda.md` e a ligação de
    verdade em `test_mail_posvenda.py`); nunca numa pasta de site (a rota
    recusa: o formulário não tem pedido, tudo ficaria `chamado_sem_pedido`).
A Entrada, os Enviados e a caixa da Goslin ligam como sempre
(`test_mail_entrada_igual.py` prova coluna a coluna). Nenhuma pasta nasce
`chamado` nem `informativo` (a 0424 dá `venda` às "vendas *" e `site` às pastas
de site da Uranyx e da Charlots).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    Chamado,
    MailMessage,
    UserRole,
)
from app.models.mail_admin import MailAcesso
from app.models.mail_atendimento import MailFolder, MailMailboxSettings, MailMessageMeta
from app.services.mail_atendimento import administracao, pastas, ponte, regras, saude
from tests.test_mail_ponte import (
    AGORA,
    GERAL,
    _config,
    categorizar,
    cena,
    contar,
    conversa,
    entrar,
    mensagens,
    meta_de,
    pedido_existe,
    rodar,
)

EMAIL = "/api/atendimento/email"
ML = "nao-responder@mercadolivre.com.br"


async def _venda(db, cena, **kw):
    """Um aviso do ML na pasta de VENDA (a 0424 dá `venda` às "vendas *")."""
    await categorizar(db, cena.geral, "vendas ml", "venda")
    mid = await entrar(
        db,
        cena.geral,
        pasta="vendas ml",
        de=kw.pop("de", ML),
        de_nome=kw.pop("de_nome", "Mercado Livre"),
        **kw,
    )
    await rodar(db)
    return await meta_de(db, mid)


# ─────────────── sem categoria: guardado, sem ligar ───────────────


async def test_pasta_criada_sem_categoria_guarda_sem_ligar(db, cena):
    mid = await entrar(db, cena.geral, pasta="problema ml", assunto="Pedido 2000012345670001")
    codigo = await entrar(
        db, cena.geral, pasta="problema ml", assunto="Seu código", corpo="Código: 482913"
    )
    await rodar(db)
    for message_id in (mid, codigo):
        m = await meta_de(db, message_id)
        assert (m.estado, m.motivo, m.regras_versao) == ("ignorado", "pasta_sem_categoria", 2)
        # Nada do e-mail em claro (como o "só contar"): nem alias, hash, loja, pedido.
        assert (m.alias_recebido, m.mid_hash, m.integration_id, m.pedidos_citados) == (
            None,
            None,
            None,
            [],
        )
        assert m.folder_id is not None and m.conversa_id is None
    assert await contar(db, AtendimentoConversa) == 0
    pasta = await db.scalar(select(MailFolder).where(MailFolder.chave == "problema ml"))
    # O conector continua mandando o que mandava (o corpo fica cifrado na Central
    # para a ponte reler quando alguém escolher a categoria).
    assert (pasta.categoria, pasta.ler, pasta.revisada) == (None, "corpo", True)


async def test_o_nosso_enviado_numa_pasta_criada_liga_como_sempre(db, cena):
    """O e-mail que NÓS mandamos (o Enviados movido para uma pasta) segue o
    caminho de sempre: a categoria é só do que chega."""
    mid = await entrar(
        db,
        cena.geral,
        pasta="problema ml",
        de="21max@tuta.com",
        para=["joao@gmail.com"],
        direcao="sent",
        corpo="Resposta avulsa.",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo, m.regras_versao) == ("ignorado", "enviado_sem_conversa", 1)


async def test_categoria_numa_pasta_do_sistema_nunca_vale(db, cena):
    """Mesmo que a linha da Entrada tenha uma categoria gravada (a rota recusa),
    a Entrada liga como sempre."""
    entrada = await categorizar(db, cena.geral, "INBOX", "informativo")
    assert regras.categoria_que_vale(entrada) is None
    mid = await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.regras_versao) == ("sem_vinculo", 1) and m.conversa_id is not None


FORM_RUI = "Nome: Rui Lima\nE-mail: rui.lima@gmail.com\nMensagem: quebrou"


async def test_sem_categoria_so_a_resposta_ao_que_a_equipe_tem_liga(db, cena):
    """Na pasta SEM categoria (revisão de 21/10): a resposta num fio que a equipe
    já atende (References/fio do Tuta) e, na pasta de SITE, o protocolo de um
    chamado que já tem conversa seguem como sempre — o cliente de uma conversa
    aberta nunca some. O e-mail novo (o formulário novo, o protocolo numa pasta
    de marketplace) fica guardado. A prova coluna a coluna de que a resposta
    liga EXATAMENTE como antes: `test_mail_pasta_como_hoje.py`."""
    primeiro = await entrar(db, cena.geral, pasta="INBOX", message_id="<p1@c.com>")
    form = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="sac@uranyx.com.br",
        de_nome="Site Uranyx",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0061] Defeito",
        corpo=FORM_RUI,
    )
    await rodar(db)
    resposta = await entrar(
        db,
        cena.geral,
        pasta="problema ml",
        assunto="Re: Pergunta sobre o pedido",
        corpo="E aí?",
        in_reply_to="<p1@c.com>",
        references=["<p1@c.com>"],
    )
    de_novo = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="rui.lima@gmail.com",
        de_nome="Rui Lima",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0061] E então?",
        corpo="Alguma novidade?",
    )
    novo = await entrar(db, cena.geral, pasta="problema ml", assunto="Outra coisa")
    outro_form = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="sac@uranyx.com.br",
        de_nome="Site Uranyx",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0062] Troca",
        corpo="Nome: Ana Lima\nE-mail: ana.lima@gmail.com\nMensagem: troca",
    )
    protocolo_fora = await entrar(
        db,
        cena.geral,
        pasta="problema ml",
        de="rui.lima@gmail.com",
        assunto="[US-26-0061] E então?",
    )
    await rodar(db)
    p1, f = await meta_de(db, primeiro), await meta_de(db, form)
    r, d = await meta_de(db, resposta), await meta_de(db, de_novo)
    assert (r.conversa_id, r.vinculado_por, r.regras_versao) == (p1.conversa_id, "referencia", 1)
    assert (d.conversa_id, d.vinculado_por, d.regras_versao) == (f.conversa_id, "protocolo", 1)
    assert d.estado == "gravado" and d.protocolo == "US-26-0061"
    for mid in (novo, outro_form, protocolo_fora):
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo, m.regras_versao, m.conversa_id) == (
            "ignorado",
            "pasta_sem_categoria",
            2,
            None,
        )
        assert (m.mid_hash, m.protocolo, m.alias_recebido) == (None, None, None)


async def test_pasta_de_site_com_chamado_guarda_tudo_sem_pedido(db, cena):
    """Por que a rota recusa Chamado em pasta de SITE e a regra nunca o sugere
    (revisão de 21/10): o formulário e a cliente do site não citam pedido de
    marketplace — na Pós-venda TUDO fica guardado como `chamado_sem_pedido`, sem
    conversa e sem tela para ligar à mão (o RF6 de hoje não volta). A categoria
    entra aqui direto no banco (a rota não deixa). "Site (como hoje)" devolve
    o RF6: os guardados voltam e viram o chamado do site."""
    await categorizar(db, cena.geral, "*uranyx sac", "chamado")
    form = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="sac@uranyx.com.br",
        de_nome="Site Uranyx",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0071] Defeito",
        corpo=FORM_RUI,
    )
    cliente = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0072] Troca",
        corpo="Quero trocar a capa.",
    )
    await rodar(db)
    for mid in (form, cliente):
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo, m.chamado_id, m.conversa_id) == (
            "ignorado",
            "chamado_sem_pedido",
            None,
            None,
        )
        assert (m.plataforma, m.regras_versao) == ("site", 2)
    assert await contar(db, AtendimentoConversa) == 0
    pasta = await db.scalar(select(MailFolder).where(MailFolder.chave == "*uranyx sac"))
    pasta.categoria = "site"
    assert await pastas.reler_ignorados(db, pasta) == 2
    await db.commit()
    await rodar(db)
    protocolos = set()
    for mid in (form, cliente):
        m = await meta_de(db, mid)
        assert (m.estado, m.regras_versao) == ("gravado", 1) and m.conversa_id is not None
        assert (await conversa(db, m.conversa_id)).plataforma == "site"
        protocolos.add(m.protocolo)
    assert protocolos == {"US-26-0071", "US-26-0072"}


async def test_como_hoje_liga_como_a_entrada_e_o_ler_fica_o_de_sempre(db, cena):
    await pedido_existe(db, cena.ml, "2000012345670021")
    await categorizar(db, cena.geral, "problema ml", "site")
    await categorizar(db, cena.geral, "financeiro", "site")
    mid = await entrar(db, cena.geral, pasta="problema ml", assunto="Pedido 2000012345670021")
    contado = await entrar(db, cena.geral, pasta="financeiro", assunto="Boleto")
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.vinculado_por, m.regras_versao) == ("gravado", "pedido", 1)
    assert (m.finalidade, m.pedido_marketplace) == ("problema", "2000012345670021")
    c = await conversa(db, m.conversa_id)
    # O de antes: "problema" é destaque e o cliente abre pendência; nada "pela pasta".
    assert c.dados["mail"]["destaque"] is True and c.aguardando_resposta is True
    assert "tipo_pela_pasta" not in c.dados["mail"]
    # "Site (como hoje)" numa pasta que só conta: continua só contando.
    assert ((await meta_de(db, contado)).motivo) == "pasta_so_contar"
    linhas = {p.nome: p for p in (await db.execute(select(MailFolder))).scalars()}
    assert (linhas["problema ml"].ler, linhas["financeiro"].ler) == ("corpo", "so_contar")


# ─────────────── venda: a conversa de e-mail daquela venda ───────────────


async def test_venda_com_pedido_no_bling_vai_para_a_conversa_de_email_da_venda(db, cena):
    pedido = "2000012345670001"
    chat = AtendimentoConversa(
        integration_id=cena.ml.id,
        plataforma="ml",
        canal="pos_venda",
        externo_id="pack-77",
        pedido_marketplace=pedido,
        situacao="respondida",
    )
    db.add(chat)
    await db.commit()
    await pedido_existe(db, cena.ml, pedido)
    m = await _venda(db, cena, assunto=f"Você vendeu! Pedido {pedido}", corpo="Parabéns.")
    assert (m.estado, m.vinculado_por, m.pedido_marketplace) == ("gravado", "pedido", pedido)
    assert (m.regras_versao, m.protocolo, m.integration_id) == (2, None, cena.ml.id)
    assert not any(a["codigo"] == "venda_pelo_numero" for a in m.alertas)
    c = await conversa(db, m.conversa_id)
    assert (c.canal, c.externo_id, c.pedido_marketplace) == (
        "email",
        f"mail-pedido:ml:{pedido}",
        pedido,
    )
    assert c.id != chat.id and ponte.e_conversa_da_ponte(c)
    assert c.sem_resposta_necessaria is True and c.aguardando_resposta is False
    assert c.dados["mail"]["tipo_email"] == "venda" and c.dados["mail"]["tipo_pela_pasta"]
    [msg] = await mensagens(db, c.id)
    assert msg.autor == "sistema"
    assert (msg.payload["mail"]["tipo_email"], msg.payload["mail"]["tipo_pela_pasta"]) == (
        "venda",
        True,
    )
    assert msg.payload["mail"]["tipo_email_inferido"] is False
    # O chat da venda não ganhou nada (o botão "E-mails N" acha pelo pedido).
    assert await mensagens(db, chat.id) == []


async def test_o_chat_da_venda_acha_o_email_da_pasta_de_venda(db, cena, client, auth_as):
    """O botão "E-mails N" do chat da venda (regra de 13/10, mantida pelo dono):
    acha o e-mail da pasta de venda pelo pedido — também o da venda que abriu
    pelo nº (fora do Bling), quando o chat aparece DEPOIS."""
    pedido = "2000012345670012"
    m = await _venda(db, cena, assunto=f"Você vendeu! Pedido {pedido}")
    assert any(a["codigo"] == "venda_pelo_numero" for a in m.alertas)
    chat = AtendimentoConversa(
        integration_id=cena.ml.id,
        plataforma="ml",
        canal="pos_venda",
        externo_id="pack-88",
        pedido_marketplace=pedido,
        situacao="respondida",
    )
    db.add(chat)
    await db.commit()
    auth_as(cena.dono)
    r = await client.get(f"{EMAIL}/venda/{chat.id}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["estado"], corpo["total"]) == ("ok", 1)
    assert corpo["itens"][0]["id"] == str(m.message_id)
    assert corpo["itens"][0]["tipo_email"] == "venda"
    # E o chat continua sem o e-mail dentro.
    assert await mensagens(db, chat.id) == []


async def test_venda_fora_do_bling_abre_pelo_numero_e_o_segundo_entra_na_mesma(db, cena):
    pedido = "2000012345670002"
    a = await _venda(db, cena, assunto=f"Você vendeu! Pedido {pedido}")
    assert (a.estado, a.pedido_marketplace, a.vinculado_por) == ("gravado", pedido, "pedido")
    codigos = {x["codigo"] for x in a.alertas}
    assert "venda_pelo_numero" in codigos and "pedido_nao_encontrado" not in codigos
    assert a.pedidos_citados == [{"pedido": pedido, "existe": False, "plataforma": "ml"}]
    c = await conversa(db, a.conversa_id)
    assert c.pedido_marketplace == pedido and c.externo_id == f"mail-pedido:ml:{pedido}"
    # O 2º e-mail do mesmo nº (o pagamento aprovado): a MESMA conversa, nunca duplicada.
    b = await _venda(db, cena, assunto=f"Pagamento aprovado do pedido {pedido}")
    assert b.conversa_id == a.conversa_id and b.vinculado_por == "pedido"
    assert await contar(db, AtendimentoConversa) == 1
    assert len(await mensagens(db, a.conversa_id)) == 2


async def test_venda_com_rotulo_de_caso_ignora_o_caso(db, cena):
    pedido = "2000012345670003"
    await pedido_existe(db, cena.ml, pedido)
    # O caso oficial do mesmo pedido, pela Entrada: conversa do CASO.
    caso = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de=ML,
        de_nome="Mercado Livre",
        assunto=f"Reclamação 5012345678 no pedido {pedido}",
        corpo="Caso 5012345678 aberto.",
    )
    await rodar(db)
    meta_caso = await meta_de(db, caso)
    assert meta_caso.protocolo == "5012345678"
    m = await _venda(
        db, cena, assunto=f"Reclamação 5012345678 no pedido {pedido}", corpo="Caso 5012345678."
    )
    assert m.protocolo is None and m.conversa_id != meta_caso.conversa_id
    c = await conversa(db, m.conversa_id)
    assert c.dados["mail"]["caso_id"] is None and c.dados["mail"]["tipo_email"] == "venda"


async def test_venda_com_dois_pedidos_fica_em_conversa_propria(db, cena):
    for n in ("2000012345670004", "2000012345670005"):
        await pedido_existe(db, cena.ml, n)
    m = await _venda(db, cena, assunto="Pedidos 2000012345670004 e 2000012345670005")
    assert m.pedido_marketplace is None and m.estado == "gravado"
    assert any(a["codigo"] == "varios_pedidos" for a in m.alertas)
    c = await conversa(db, m.conversa_id)
    assert c.externo_id == f"mail:{m.message_id}" and c.dados["mail"]["tipo_email"] == "venda"


async def test_venda_sem_numero_fica_em_conversa_propria_nunca_no_fio(db, cena):
    fio = {"conversation_id": "FIO-VENDAS-1"}
    a = await _venda(db, cena, assunto="Você vendeu!", tuta=fio)
    b = await _venda(db, cena, assunto="Você vendeu!", tuta=fio)
    assert a.estado == b.estado == "gravado"
    assert a.conversa_id != b.conversa_id
    for m in (a, b):
        c = await conversa(db, m.conversa_id)
        assert c.externo_id == f"mail:{m.message_id}" and c.pedido_marketplace is None


async def test_venda_entra_na_conversa_de_email_do_pedido_e_o_tipo_nao_desce(db, cena):
    pedido = "2000012345670006"
    await pedido_existe(db, cena.ml, pedido)
    cliente = await entrar(db, cena.geral, pasta="INBOX", assunto=f"Pedido {pedido} não chegou")
    await rodar(db)
    meta_cliente = await meta_de(db, cliente)
    m = await _venda(db, cena, assunto=f"Você vendeu! Pedido {pedido}")
    assert m.conversa_id == meta_cliente.conversa_id and m.vinculado_por == "pedido"
    c = await conversa(db, m.conversa_id)
    # A conversa do cliente (chamado) não vira "venda", nem ganha a marca da pasta.
    assert c.dados["mail"]["tipo_email"] == "chamado"
    assert "tipo_pela_pasta" not in c.dados["mail"]
    assert c.aguardando_resposta is True
    assert [x.payload["mail"].get("tipo_pela_pasta") for x in await mensagens(db, c.id)] == [
        None,
        True,
    ]


async def test_venda_suspeita_fica_isolada(db, cena):
    pedido = "2000012345670007"
    await pedido_existe(db, cena.ml, pedido)
    m = await _venda(
        db,
        cena,
        de="suporte@mercadolivre-br.com",
        assunto=f"Pedido {pedido} bloqueado",
    )
    assert m.suspeito and m.pedido_marketplace is None
    assert m.vinculado_por == "suspeito"
    codigos = {a["codigo"] for a in m.alertas}
    assert {"suspeito_sem_vinculo", "suspeito_sem_agrupamento"} <= codigos
    assert (await conversa(db, m.conversa_id)).externo_id == f"mail:{m.message_id}"


async def test_venda_da_loja_sem_integracao_tem_a_ficha_na_chave(db, cena):
    pedido = "2000012345670008"
    a = await _venda(db, cena, para=["16tr@tuta.com"], assunto=f"Você vendeu! Pedido {pedido}")
    assert a.integration_id is None and a.store_info_id is not None
    c = await conversa(db, a.conversa_id)
    assert c.externo_id == f"mail-loja:{a.store_info_id}:mail-pedido:ml:{pedido}"
    b = await _venda(db, cena, para=["16tr@tuta.com"], assunto=f"Pagamento do pedido {pedido}")
    assert b.conversa_id == a.conversa_id


# ─────────────── informativo: a linha do item da pasta ───────────────


async def test_informativo_vira_linha_sem_conversa(db, cena):
    await categorizar(db, cena.geral, "novidades ml", "informativo")
    mid = await entrar(
        db,
        cena.geral,
        pasta="novidades ml",
        de="novidades@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto="Novidades da semana",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo, m.regras_versao) == ("informativo", None, 2)
    assert (m.conversa_id, m.mensagem_id) == (None, None)
    assert (m.integration_id, m.plataforma, m.alias_recebido) == (
        cena.ml.id,
        "ml",
        "21max@tuta.com",
    )
    assert m.mid_hash and m.folder_id is not None
    assert await contar(db, AtendimentoConversa) == 0
    assert await contar(db, AtendimentoMensagem) == 0
    # O tipo, sem mensagem, é o da categoria.
    tipos = ponte.classificacao.tipos_de_meta(m)
    assert (tipos["tipo_email"], tipos["tipo_pela_pasta"]) == ("informativo", True)


async def test_informativo_seguranca_vence_e_sem_loja_continua_linha(db, cena):
    await categorizar(db, cena.geral, "novidades ml", "informativo")
    codigo = await entrar(
        db,
        cena.geral,
        pasta="novidades ml",
        de="no-reply@mercadolivre.com.br",
        assunto="Seu código de verificação",
        corpo="Use 482913 para entrar.",
    )
    sem_loja = await entrar(db, cena.geral, pasta="novidades ml", para=["hans21@tuta.com"])
    await rodar(db)
    assert (await meta_de(db, codigo)).estado == "seguranca"
    m = await meta_de(db, sem_loja)
    assert (m.estado, m.integration_id, m.store_info_id) == ("informativo", None, None)


async def test_informativo_duplicado_entre_caixas_e_uma_linha_so(db, cena):
    await categorizar(db, cena.geral, "novidades ml", "informativo")
    await categorizar(db, cena.goslin, "novidades ml", "informativo")
    primeiro = await entrar(db, cena.geral, pasta="novidades ml", message_id="<news@ml.com>")
    await rodar(db)
    copia = await entrar(
        db,
        cena.goslin,
        pasta="novidades ml",
        para=["mia30@tuta.com"],
        message_id="<news@ml.com>",
    )
    await rodar(db)
    m = await meta_de(db, copia)
    assert (m.estado, m.duplicado_de) == ("duplicado", primeiro)


async def test_informativos_na_aba_email(db, cena, client, auth_as):
    novidades = await categorizar(db, cena.geral, "novidades ml", "informativo")
    vazia = await categorizar(db, cena.geral, "comunicados", "informativo")
    ids = []
    for n, para in enumerate(("21max@tuta.com", "22max@tuta.com")):
        ids.append(
            await entrar(
                db,
                cena.geral,
                pasta="novidades ml",
                de="novidades@mercadolivre.com.br",
                para=[para],
                assunto=f"Novidades {n}",
                corpo="Confira.",
                recebido_em=AGORA - timedelta(minutes=10 - n),
            )
        )
    await rodar(db)
    auth_as(cena.dono)
    r = await client.get(f"{EMAIL}/informativos")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    itens = {i["pasta_id"]: i for i in r.json()["itens"]}
    # O item com e-mail mais novo em cima; o vazio no fim.
    assert list(itens) == [str(novidades.id), str(vazia.id)]
    assert itens[str(novidades.id)]["total"] == 2 and itens[str(novidades.id)]["ultimo_em"]
    assert itens[str(novidades.id)]["caixa"] == "Geral — Tuta"
    # As lojas do item (a Barbosa do ML e a da Shopee têm o mesmo nome).
    assert itens[str(novidades.id)]["lojas"] == ["Barbosa"]
    assert (itens[str(vazia.id)]["total"], itens[str(vazia.id)]["lojas"]) == (0, [])
    # As linhas de UM item: a mais nova em cima, sem o texto.
    r = await client.get(f"{EMAIL}/emails", params={"fila": "informativo", "pasta": novidades.id})
    linhas = r.json()["itens"]
    assert [x["id"] for x in linhas] == [str(ids[1]), str(ids[0])]
    assert linhas[0]["assunto"] == "Novidades 1" and "texto" not in linhas[0]
    assert (linhas[0]["tipo_email"], linhas[0]["tipo_pela_pasta"]) == ("informativo", True)
    assert (await client.get(f"{EMAIL}/emails", params={"pasta": vazia.id})).json()["itens"] == []
    # "Ver o e-mail" e a contagem da fila.
    detalhe = (await client.get(f"{EMAIL}/emails/{ids[0]}")).json()
    assert detalhe["texto"] == "Confira." and detalhe["estado"] == "informativo"
    assert (await client.get(f"{EMAIL}/filas")).json()["informativo"] == 2


async def test_informativos_so_para_quem_mexe(db, cena, client, auth_as, make_user, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outra@davinci-test.com")
    auth_as(await make_user(role=UserRole.USER))
    r = await client.get(f"{EMAIL}/informativos")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "atendimento_so_quem_mexe"


async def test_tipo_permitido_filtra_a_linha_informativa_sem_mensagem(db, cena, make_user):
    await categorizar(db, cena.geral, "novidades ml", "informativo")
    mid = await entrar(db, cena.geral, pasta="novidades ml", assunto="Novidades")
    await rodar(db)
    pessoa = await make_user(role=UserRole.USER)
    acesso = MailAcesso(
        mailbox_id=cena.geral.id,
        user_id=pessoa.id,
        modo="padrao",
        lojas_permitidas=[],
        lojas_bloqueadas=[],
        tipos_permitidos=["chamado", "venda"],
        pedidos_permitidos=[],
    )
    db.add(acesso)
    await db.commit()

    async def visivel() -> bool:
        return bool(
            await db.scalar(
                select(func.count()).where(
                    MailMessageMeta.message_id == mid,
                    administracao.filtro_mensagem(pessoa, MailMessageMeta.message_id),
                )
            )
        )

    assert await visivel() is False
    acesso.tipos_permitidos = ["informativo"]
    await db.commit()
    assert await visivel() is True


# ─────────────── chamado: a Pós-venda (o contrato) ───────────────


class _Espia:
    """O `posvenda.ligar_ao_chamado` de mentira: guarda o que a ponte mandou."""

    def __init__(self, resposta=None, erro: Exception | None = None):
        self.chamadas: list[dict] = []
        self.resposta = resposta
        self.erro = erro

    async def __call__(self, session, email, pasta, **kw):
        self.chamadas.append({"email": email, "pasta": pasta, **kw})
        if self.erro is not None:
            raise self.erro
        return self.resposta


async def _chamado_ml(db, pedido: str) -> Chamado:
    ch = Chamado(origem="vendas", pedido_marketplace=pedido, plataforma="ml", conta="Barbosa")
    db.add(ch)
    await db.commit()
    return ch


async def _na_pasta_chamado(db, cena, **kw) -> UUID:
    await categorizar(db, cena.geral, "problema ml", "chamado")
    return await entrar(db, cena.geral, pasta="problema ml", **kw)


async def test_chamado_chama_o_posvenda_pelo_contrato(db, cena, monkeypatch):
    from app.services.mail_atendimento import posvenda

    pedido = "2000012345670011"
    await pedido_existe(db, cena.ml, pedido)
    ch = await _chamado_ml(db, pedido)
    espia = _Espia(
        posvenda.LigacaoChamado(chamado_id=ch.id, vinculado_por="chamado_pedido", pedido=pedido)
    )
    monkeypatch.setattr(posvenda, "ligar_ao_chamado", espia)
    mid = await _na_pasta_chamado(
        db,
        cena,
        assunto=f"Reclamação 5012345678 no pedido {pedido}",
        corpo="Chegou quebrado. https://x.com/login?token=abc",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo, m.chamado_id, m.vinculado_por) == (
        "gravado",
        None,
        ch.id,
        "chamado_pedido",
    )
    assert (m.pedido_marketplace, m.regras_versao) == (pedido, 2)
    # Nunca conversa do /atendimento.
    assert (m.conversa_id, m.mensagem_id) == (None, None)
    assert await contar(db, AtendimentoConversa) == 0
    [chamada] = espia.chamadas
    assert chamada["pedido"] == pedido and chamada["pedidos_citados"] == [pedido]
    assert chamada["caso_id"] == "5012345678"
    assert chamada["pasta"].nome == "problema ml"
    assert chamada["rota"].integration_id == cena.ml.id
    assert chamada["meta"].message_id == mid
    # Só o texto protegido (o link de acesso fora).
    assert "token=abc" not in chamada["protegido"].texto
    tipos = ponte.classificacao.tipos_de_meta(m)
    assert (tipos["tipo_email"], tipos["tipo_pela_pasta"]) == ("chamado", True)


async def test_chamado_sem_ligacao_guarda_com_o_motivo_do_posvenda(db, cena, monkeypatch):
    from app.services.mail_atendimento import posvenda

    for motivo, esperado in ((None, "chamado_sem_pedido"), ("chamado_sem_chamado",) * 2):
        espia = _Espia(posvenda.LigacaoChamado(chamado_id=None, motivo=motivo))
        monkeypatch.setattr(posvenda, "ligar_ao_chamado", espia)
        mid = await _na_pasta_chamado(db, cena, assunto="Dúvida")
        await rodar(db)
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo, m.chamado_id, m.conversa_id) == (
            "ignorado",
            esperado,
            None,
            None,
        )
        # A loja fica (a pessoa liga à mão na Pós-venda).
        assert m.integration_id == cena.ml.id and len(espia.chamadas) == 1


async def test_chamado_suspeito_ou_sem_loja_nem_chega_ao_posvenda(db, cena, monkeypatch):
    from app.services.mail_atendimento import posvenda

    espia = _Espia(posvenda.LigacaoChamado(chamado_id=None))
    monkeypatch.setattr(posvenda, "ligar_ao_chamado", espia)
    suspeito = await _na_pasta_chamado(
        db, cena, de="suporte@mercadolivre-br.com", de_nome="Mercado Livre"
    )
    sem_loja = await _na_pasta_chamado(db, cena, para=["hans21@tuta.com"])
    await rodar(db)
    m = await meta_de(db, suspeito)
    assert (m.estado, m.motivo) == ("ignorado", "chamado_sem_pedido")
    assert any(a["codigo"] == "suspeito_sem_vinculo" for a in m.alertas)
    assert (await meta_de(db, sem_loja)).estado == "sem_loja"
    assert espia.chamadas == []


async def test_chamado_com_erro_no_posvenda_nao_deixa_nada_pela_metade(db, cena, monkeypatch):
    from app.services.mail_atendimento import posvenda

    monkeypatch.setattr(posvenda, "ligar_ao_chamado", _Espia(erro=RuntimeError("quebrou")))
    mid = await _na_pasta_chamado(db, cena, assunto="Dúvida")
    resumo = await rodar(db)
    assert resumo["erros"] == 1
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo, m.chamado_id) == ("erro", "RuntimeError", None)
    # O "Reprocessar" leva de novo pela ponte.
    monkeypatch.setattr(
        posvenda, "ligar_ao_chamado", _Espia(posvenda.LigacaoChamado(chamado_id=None))
    )
    assert await ponte.reprocessar(db, mid) == "ignorado"
    await db.commit()
    assert (await meta_de(db, mid)).motivo == "chamado_sem_pedido"


# ─────────────── reler o que ficou guardado ───────────────


async def test_reler_so_o_que_ficou_guardado_pela_categoria(db, cena):
    guardados = [await entrar(db, cena.geral, pasta="problema ml") for _ in range(2)]
    entrada = await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)
    pasta = await db.scalar(select(MailFolder).where(MailFolder.chave == "problema ml"))
    # Sem categoria: nada a reler.
    assert await pastas.reler_ignorados(db, pasta) == 0
    pasta.categoria = "venda"
    assert await pastas.reler_ignorados(db, pasta) == 2
    await db.commit()
    for mid in guardados:
        assert await meta_de(db, mid) is None
    assert (await meta_de(db, entrada)).estado == "sem_vinculo"
    pendentes = await ponte.pendentes(db)
    assert set(guardados) <= set(pendentes)
    await rodar(db)
    assert {(await meta_de(db, mid)).estado for mid in guardados} == {"gravado"}


async def _ponte_desde_30_dias(db, cena) -> None:
    cfg = await db.scalar(
        select(MailMailboxSettings).where(MailMailboxSettings.mailbox_id == cena.geral.id)
    )
    cfg.ponte_desde = AGORA - timedelta(days=30)
    await db.commit()


async def test_reler_so_os_guardados_dos_ultimos_dias(db, cena, client, auth_as):
    """Revisão de 21/10: categorizar a pasta depois de semanas não pode virar
    centenas de conversas de uma vez — relê só os guardados dos últimos
    `RELER_DIAS` dias (a tela mostra o mesmo número); o mais antigo fica
    guardado (a pessoa ainda o relê, um a um)."""
    await _ponte_desde_30_dias(db, cena)
    antigo = await entrar(
        db, cena.geral, pasta="problema ml", recebido_em=AGORA - timedelta(days=10)
    )
    recente = await entrar(
        db, cena.geral, pasta="problema ml", recebido_em=AGORA - timedelta(days=2)
    )
    await rodar(db)
    assert {(await meta_de(db, m)).motivo for m in (antigo, recente)} == {"pasta_sem_categoria"}
    auth_as(cena.dono)
    item = next(
        p
        for p in (await client.get(f"{EMAIL}/pastas")).json()["itens"]
        if p["nome"] == "problema ml"
    )
    assert (item["guardados"], item["total_30d"]) == (1, 2)
    r = await client.patch(f"{EMAIL}/pastas/{item['id']}", json={"categoria": "site"})
    assert r.status_code == 200 and r.json()["relidos"] == 1
    assert await meta_de(db, recente) is None
    assert (await meta_de(db, antigo)).motivo == "pasta_sem_categoria"
    await rodar(db)
    assert (await meta_de(db, recente)).conversa_id is not None
    assert await ponte.reprocessar(db, antigo) == "sem_vinculo"
    await db.commit()
    assert (await meta_de(db, antigo)).conversa_id is not None


async def test_o_que_acabou_de_chegar_passa_na_frente_dos_relidos(db, cena):
    """Revisão de 21/10: o guardado que a categoria nova mandou reler (chegou à
    Central há dias) vai DEPOIS do e-mail que acabou de chegar — a Entrada e o
    cliente nunca esperam o acumulado."""
    await _ponte_desde_30_dias(db, cena)
    relido = await entrar(
        db, cena.geral, pasta="problema ml", recebido_em=AGORA - timedelta(days=3)
    )
    await rodar(db)
    message = await db.get(MailMessage, relido)
    message.created_at = AGORA - timedelta(days=3)
    await db.commit()
    pasta = await categorizar(db, cena.geral, "problema ml", "site")
    assert await pastas.reler_ignorados(db, pasta) == 1
    await db.commit()
    novo = await entrar(db, cena.geral, pasta="INBOX")
    assert await ponte.pendentes(db, limite=1) == [novo]
    assert await ponte.pendentes(db) == [novo, relido]
    resumo = await ponte.rodar(db, limite=1)
    assert resumo["processados"] == 1 and await meta_de(db, relido) is None
    assert (await meta_de(db, novo)).estado == "sem_vinculo"


async def test_reprocessar_aceita_o_guardado_pela_categoria(db, cena):
    mid = await entrar(db, cena.geral, pasta="vendas ml", assunto="Você vendeu!")
    await rodar(db)
    assert (await meta_de(db, mid)).motivo == "pasta_sem_categoria"
    # Ainda sem categoria: reler dá o mesmo.
    assert await ponte.reprocessar(db, mid) == "ignorado"
    await db.commit()
    await categorizar(db, cena.geral, "vendas ml", "venda")
    assert await ponte.reprocessar(db, mid) == "gravado"
    await db.commit()
    # O que já está numa conversa nunca é refeito.
    assert await ponte.reprocessar(db, mid) == "gravado"
    assert await contar(db, AtendimentoMensagem) == 1


async def test_patch_rele_o_guardado_e_o_chamado_sem_pedido(db, cena, client, auth_as):
    from app.services.mail_atendimento import posvenda

    await categorizar(db, cena.geral, "problema ml", "chamado")
    sem_pedido = await entrar(db, cena.geral, pasta="problema ml", assunto="Dúvida")
    await rodar(db)
    assert (await meta_de(db, sem_pedido)).motivo in posvenda.MOTIVOS_PARA_LIGAR
    pasta = await db.scalar(select(MailFolder).where(MailFolder.chave == "problema ml"))
    auth_as(cena.dono)
    # Tirar a categoria (null = "não vincular") não relê nada.
    r = await client.patch(f"{EMAIL}/pastas/{pasta.id}", json={"categoria": None})
    assert r.status_code == 200 and r.json()["relidos"] == 0
    assert (await meta_de(db, sem_pedido)) is not None
    # Escolher outra: o guardado volta pela ponte com a categoria nova.
    r = await client.patch(f"{EMAIL}/pastas/{pasta.id}", json={"categoria": "venda"})
    assert r.status_code == 200 and r.json()["relidos"] == 1
    await rodar(db)
    m = await meta_de(db, sem_pedido)
    assert m.estado == "gravado" and m.conversa_id is not None


# ─────────────── as rotas das pastas ───────────────


async def test_pastas_mostram_categoria_sugestao_e_trava(db, cena, client, auth_as):
    for pasta in ("INBOX", "vendas ml", "*uranyx sac", "financeiro", "coisas", "Trash"):
        await entrar(db, cena.geral, pasta=pasta)
    await rodar(db)
    auth_as(cena.dono)
    corpo = (await client.get(f"{EMAIL}/pastas")).json()
    itens = {p["nome"]: p for p in corpo["itens"]}
    assert [c["valor"] for c in corpo["categorias"]] == [
        "informativo",
        "chamado",
        "venda",
        "site",
    ]
    assert {c["rotulo"] for c in corpo["categorias"]} >= {
        "Chamado (Pós-venda)",
        "Site (como hoje)",
    }
    assert corpo["reler_dias"] == 7
    esperado = {
        "INBOX": (True, None),
        "Trash": (True, None),
        "vendas ml": (False, "venda"),
        "*uranyx sac": (False, "site"),
        "financeiro": (False, None),
        "coisas": (False, None),
    }
    for nome, (travada, sugestao) in esperado.items():
        assert (itens[nome]["travada"], itens[nome]["categoria_sugerida"]) == (
            travada,
            sugestao,
        ), nome
        assert itens[nome]["categoria"] is None
    assert itens["vendas ml"]["total_30d"] == 1 and itens["vendas ml"]["guardados"] == 1
    assert itens["INBOX"]["guardados"] == 0
    r = await client.patch(
        f"{EMAIL}/pastas/{itens['vendas ml']['id']}", json={"categoria": "venda"}
    )
    assert r.status_code == 200, r.text
    vendas = next(
        p for p in (await client.get(f"{EMAIL}/pastas")).json()["itens"] if p["nome"] == "vendas ml"
    )
    assert (vendas["categoria"], vendas["categoria_rotulo"], vendas["revisada"]) == (
        "venda",
        "Venda",
        True,
    )
    assert vendas["categoria_por"] == (cena.dono.name or cena.dono.email)
    assert vendas["categoria_em"]


async def test_patch_categoria_recusas(db, cena, client, auth_as, make_user):
    for pasta in ("INBOX", "coisas", "*uranyx sac", "financeiro", "vendas ml"):
        await entrar(db, cena.geral, pasta=pasta)
    await rodar(db)
    linhas = {p.nome: p for p in (await db.execute(select(MailFolder))).scalars()}
    auth_as(cena.dono)

    async def patch(nome, **corpo):
        return await client.patch(f"{EMAIL}/pastas/{linhas[nome].id}", json=corpo)

    for nome, corpo, codigo in (
        ("vendas ml", {"categoria": "suporte"}, "categoria_invalida"),
        ("INBOX", {"categoria": "informativo"}, "categoria_em_pasta_do_sistema"),
        ("coisas", {"categoria": "venda"}, "categoria_venda_sem_plataforma"),
        ("*uranyx sac", {"categoria": "venda"}, "categoria_venda_sem_plataforma"),
        ("*uranyx sac", {"categoria": "chamado"}, "categoria_chamado_em_site"),
        ("coisas", {"plataforma": "site", "categoria": "chamado"}, "categoria_chamado_em_site"),
    ):
        r = await patch(nome, **corpo)
        assert r.status_code == 422 and r.json()["detail"]["code"] == codigo, nome
    # Nada foi gravado pelas recusas.
    for nome in ("*uranyx sac", "coisas"):
        p = await db.scalar(
            select(MailFolder)
            .where(MailFolder.id == linhas[nome].id)
            .execution_options(populate_existing=True)
        )
        assert (p.categoria, p.plataforma_manual) == (None, None), nome
    # O site liga como antes com "Site (como hoje)"; Chamado vale numa pasta de marketplace.
    r = await patch("*uranyx sac", categoria="site")
    assert r.status_code == 200 and (r.json()["categoria"], r.json()["ler"]) == (
        "site",
        "corpo",
    )
    assert (await patch("vendas ml", categoria="chamado")).status_code == 200
    # Trocar a plataforma da pasta de Chamado para site: recusado.
    r = await patch("vendas ml", plataforma="site")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "categoria_chamado_em_site"
    # A pasta "só contar" com categoria passa a mandar o corpo; sem ela, volta.
    # "Site (como hoje)" não: continua só contando.
    r = await patch("financeiro", categoria="site")
    assert r.status_code == 200 and r.json()["ler"] == "so_contar"
    r = await patch("financeiro", categoria="informativo")
    assert r.status_code == 200 and r.json()["ler"] == "corpo"
    r = await patch("financeiro", categoria=None)
    assert r.status_code == 200 and (r.json()["ler"], r.json()["categoria"]) == (
        "so_contar",
        None,
    )
    # Venda com a plataforma escolhida por pessoa vale; tirar a plataforma da
    # pasta de venda é recusado (e nada é gravado).
    assert (await patch("coisas", plataforma="ml", categoria="venda")).status_code == 200
    r = await patch("coisas", plataforma=None)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "categoria_venda_sem_plataforma"
    coisas = await db.scalar(
        select(MailFolder)
        .where(MailFolder.id == linhas["coisas"].id)
        .execution_options(populate_existing=True)
    )
    assert (coisas.plataforma_manual, coisas.categoria) == ("ml", "venda")
    # Só admin.
    auth_as(await make_user(role=UserRole.USER))
    assert (await patch("vendas ml", categoria="venda")).status_code == 403


async def test_saude_conta_as_pastas_sem_categoria_da_semana(db, cena):
    await entrar(db, cena.geral, pasta="problema ml")
    await entrar(db, cena.geral, pasta="mensagens ml")
    await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)
    linha = await saude.linha_da_caixa(db, await db.get(type(cena.geral), cena.geral.id))
    assert linha["pastas_sem_categoria"] == 2


# ─────────────── a regra só sugere ───────────────

# As 25 pastas de produção (21/10/2026) pelo nome e o tipo do Tuta → a sugestão.
# Decisão 9 do dono: as caixas de site → "Site (como hoje)" (o que faziam até
# a subida); mensagens/problema → nada ("o resto já vem esse novo padrão");
# NUNCA "Chamado" (o dono não liga pasta a chamado por enquanto, o "ligar à
# mão" não tem tela, e no site o chamado nunca acha pedido).
SUGESTOES_DE_PRODUCAO = [
    ("vendas shopee", "0", "venda"),
    ("vendas ml", "0", "venda"),
    ("vendas amazon", "0", "venda"),
    ("vendas temu", "0", "venda"),
    ("vendas ali", "0", "venda"),
    ("vendas magalu", "0", "venda"),
    ("mensagens ml", "0", None),
    ("mensagens amazon", "0", None),
    ("problema ml", "0", None),
    ("problema shopee", "0", None),
    ("problema tiktok", "0", None),
    ("*makisa", "0", "site"),
    ("*uranyx sac", "0", "site"),
    ("*poofy", "0", "site"),
    ("envio", "0", None),
    ("financeiro", "0", None),
    ("devoluçoes", "0", None),
    ("anuncio problema", "0", None),
    ("envio erro", "0", None),
    ("contabilidade", "0", None),
    ("INBOX", "1", None),
    ("INBOX", "1", None),
    ("Sent", "2", None),
    ("Trash", "3", None),
    ("Spam", "5", None),
]


@pytest.mark.parametrize(("nome", "tipo_tuta", "sugestao"), SUGESTOES_DE_PRODUCAO)
def test_categoria_sugerida_das_pastas_de_producao(nome, tipo_tuta, sugestao):
    classe = regras.classificar(nome, tipo_tuta, regras.regras_padrao())
    assert regras.categoria_sugerida(classe, tipo_tuta) == sugestao
    assert regras.categoria_sugerida(classe, tipo_tuta) != "chamado"


def test_reclassificar_nunca_mexe_na_categoria_e_o_ler_segue_a_pessoa():
    r = regras.regras_padrao()

    def pasta(nome, tipo_tuta="0", **kw):
        return SimpleNamespace(
            nome=nome,
            tipo_tuta=tipo_tuta,
            plataforma_manual=None,
            finalidade_manual=None,
            ignorar=False,
            categoria=None,
            plataforma=None,
            finalidade=None,
            ler="so_contar",
            revisada=False,
            **kw,
        )

    # Sem categoria: o `ler` de sempre (o conector manda o que mandava).
    for nome, ler in (
        ("mensagens ml", "corpo"),
        ("financeiro", "so_contar"),
        ("coisas", "so_contar"),
    ):
        p = pasta(nome)
        regras.reclassificar(p, r)
        assert (p.ler, p.categoria) == (ler, None), nome
    # Com categoria: o corpo entra (a pessoa liberou); a regra nunca a apaga.
    p = pasta("financeiro")
    p.categoria = "informativo"
    regras.reclassificar(p, r)
    assert (p.ler, p.categoria) == ("corpo", "informativo")
    p.nome = "financeiro 2"
    regras.reclassificar(p, r)
    assert p.categoria == "informativo"
    # "Site (como hoje)": o `ler` de sempre (a pasta que só conta continua só contando).
    for nome, ler in (("financeiro", "so_contar"), ("mensagens ml", "corpo"), ("*poofy", "corpo")):
        c = pasta(nome)
        c.categoria = "site"
        regras.reclassificar(c, r)
        assert (c.ler, regras.categoria_que_vale(c)) == (ler, "site"), nome
    # Ignorar vence a categoria; a pasta do sistema nunca tem categoria valendo.
    p.ignorar = True
    regras.reclassificar(p, r)
    assert p.ler == "nao"
    for nome, tipo, ler in (("INBOX", "1", "corpo"), ("Trash", "3", "so_contar")):
        s = pasta(nome, tipo)
        s.categoria = "venda"
        regras.reclassificar(s, r)
        assert (s.ler, regras.categoria_que_vale(s)) == (ler, None), nome
    assert regras.e_criada("0") and regras.e_criada("9") and not regras.e_criada("8")


def test_caixa_crua_confia_na_seguranca_da_ponte_so_depois_dela():
    """A Caixa (o índice) só usa o veredito de segurança da ponte quando ela
    passou desse passo: a linha informativa e o chamado guardado passaram; a
    pasta sem categoria para ANTES (vale a regra estrita do índice)."""
    from app.services.mail_atendimento import indice

    assert "informativo" in indice._ESTADOS_DEPOIS_DA_SEGURANCA
    assert {"chamado_sem_pedido", "chamado_sem_chamado"} <= set(
        indice._IGNORADOS_DEPOIS_DA_SEGURANCA
    )
    assert "pasta_sem_categoria" not in indice._IGNORADOS_DEPOIS_DA_SEGURANCA
