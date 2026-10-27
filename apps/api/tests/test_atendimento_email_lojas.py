# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""As PASTAS de e-mail de cada LOJA na barra de lojas da Caixa (27/10/2026).

O Eduardo, 27/10: "botar um AMPLIÁVEL que vai abrir os e-mails embaixo de
cada loja"; no mesmo dia, os grupos embaixo de cada loja são as três pastas do
Tuta que ele unificou — Problemas ("problemas"), Envios ("envio") e Devolução
("devoluçoes") —, cada uma com "opção de encolher ou abrir", só com os
e-mails daquela loja (a escolha é UMA constante, `GRUPOS_EMAIL_NA_BARRA_DE_LOJAS`).
Toda loja tem as três caixas, mesmo vazias, e a vazia diz por quê. A aba
E-mail inteira é só de admin (quem não é admin vê e-mail SÓ pela barra).

O que não pode falhar:
  • a pasta casa com o grupo pelo nome inteiro (ou a chave), sem acento e sem
    maiúsculas — "envio erro" e "problema ml" não são os grupos;
  • a conversa entra no grupo pela pasta do e-mail RECEBIDO mais recente
    (o enviado não conta) — num grupo só;
  • GET …/email/contagens?por_loja=true — TODA loja (as integrações no escopo
    e as fichas sem integração), com os números de cada grupo (conversas e
    linhas informativas), o endereço da loja para a ponte, os e-mails dos
    últimos dias e o status de cada pasta; os números de cima são os mesmos do
    caminho sem `por_loja`;
  • a ficha cuja integração existe (o par nome + plataforma, sem acento, sem
    o nome da plataforma na frente) soma na loja da integração;
  • GET /conversas?caixa=email&…&grupo_email=… — a lista da loja no grupo;
    as LINHAS informativas em GET …/email/lojas/linhas (e o e-mail inteiro em
    …/lojas/linhas/{id}), para quem vê a loja;
  • quem não é admin: só a lista de UMA loja num grupo da barra (o resto da
    aba E-mail é 403 `aba_email_so_admin`); admin: tudo como antes;
  • a loja bloqueada na política de acesso e a equipe cortam os números.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.models import (
    AtendimentoConversa,
    MailFolder,
    MailMessage,
    StoreInfo,
    UserRole,
)
from app.models.enums import IntegrationPlatform
from app.models.mail_atendimento import MailMessageMeta
from app.security.cipher import encrypt_json
from app.services.mail_atendimento import barra_lojas
from app.services.mail_atendimento import constantes as mail_constantes
from tests.test_atendimento_separa_email import URL, _ids
from tests.test_atendimento_separa_email import admin as admin
from tests.test_atendimento_separa_email import quem_le as quem_le
from tests.test_atendimento_separa_email_ui import _historico
from tests.test_mail_admin_atendimento import _salvar
from tests.test_mail_ponte import AGORA, _config, _integ, cena, conversa, entrar, meta_de, rodar

EMAIL = f"{URL}/email"
SO_ADMIN = "aba_email_so_admin"
GRUPOS = ["problemas", "envios", "devolucao", "informativo"]


def _vazio(**k) -> dict:
    return {"emails": 0, "abertas": 0, "aguardando": 0, "total": 0, **k}


async def _pasta(db, cena, nome: str, categoria: str | None = None, caixa=None) -> MailFolder:
    caixa = caixa or cena.geral
    achada = await db.scalar(
        select(MailFolder).where(MailFolder.mailbox_id == caixa.id, MailFolder.chave == nome)
    )
    if achada is None:
        achada = MailFolder(
            mailbox_id=caixa.id, chave=nome, nome=nome, caminho=nome, tipo_tuta="0", ler="so_contar"
        )
        db.add(achada)
    if categoria is not None:
        achada.categoria = categoria
        achada.ler = "corpo"
    await db.flush()
    return achada


async def _na_pasta(db, cena, conversa_id, nome: str) -> MailFolder:
    """Os e-mails da conversa na pasta do Tuta (como o dono fez ao unificar)."""
    folder = await _pasta(db, cena, nome)
    for meta in (
        await db.execute(select(MailMessageMeta).where(MailMessageMeta.conversa_id == conversa_id))
    ).scalars():
        meta.folder_id = folder.id
    await db.commit()
    return folder


async def _email_na_conversa(db, cena, conversa_id, *, pasta: str, direcao: str, minutos: int):
    """Mais um e-mail (meta) na conversa, numa pasta, há `minutos` minutos."""
    c = await db.get(AtendimentoConversa, conversa_id)
    folder = await _pasta(db, cena, pasta)
    msg = MailMessage(
        id=uuid4(),
        mailbox_id=cena.geral.id,
        source_id=f"tuta:X{uuid4().hex[:8]}/Y",
        direction=direcao,
        received_at=AGORA - timedelta(minutes=minutos),
        content_enc=encrypt_json({"subject": "x", "text": "y", "folder": pasta}),
        attachment_count=0,
    )
    db.add(msg)
    await db.flush()
    db.add(
        MailMessageMeta(
            message_id=msg.id,
            mailbox_id=cena.geral.id,
            folder_id=folder.id,
            estado="gravado",
            integration_id=c.integration_id,
            conversa_id=c.id,
        )
    )
    await db.commit()


async def _ficha_sem_integracao(db, cena) -> tuple[str, str]:
    """O e-mail da JLAS2 (ficha SEM integração, alias 16tr): a conversa da ficha."""
    mid = await entrar(db, cena.geral, para=["16tr@tuta.com"], de="ana@gmail.com")
    await rodar(db)
    meta = await meta_de(db, mid)
    c = await conversa(db, meta.conversa_id)
    assert c.integration_id is None and meta.store_info_id is not None
    return str(c.id), str(meta.store_info_id)


async def _pastas_ligadas(db, cena) -> dict[str, MailFolder]:
    """As três pastas da barra como o dono deixou (27/10): Informativo, lendo o corpo."""
    return {
        nome: await _pasta(db, cena, nome, categoria="informativo")
        for nome in ("problemas", "envio", "devoluçoes")
    }


async def _resposta(client) -> dict:
    r = await client.get(f"{EMAIL}/contagens", params={"por_loja": "true"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


async def _por_loja(client) -> dict:
    return {
        (i["integration_id"] or i["store_info_id"]): i
        for i in (await _resposta(client))["por_loja"]
    }


def test_os_grupos_sao_uma_constante_so():
    assert [g[0] for g in mail_constantes.GRUPOS_EMAIL_NA_BARRA_DE_LOJAS] == GRUPOS
    assert [g.rotulo for g in barra_lojas.grupos()] == [
        "Problemas",
        "Envios",
        "Devolução",
        "Informativo",
    ]
    # Pelo nome inteiro, sem acento e sem diferenciar maiúsculas (a grafia do Tuta).
    casos = {
        "problemas": "problemas",
        "Problemas": "problemas",
        " envio ": "envios",
        "ENVIO": "envios",
        "devoluçoes": "devolucao",
        "Devoluções": "devolucao",
        "devolucoes": "devolucao",
        "Informativo": "informativo",
        "informativos": None,
        "envio erro": None,
        "problema ml": None,
        "problema": None,
        "INBOX": None,
    }
    for nome, grupo in casos.items():
        assert barra_lojas.grupo_da_pasta(nome) == grupo, nome
    # Pelo caminho (a última parte) e pela chave.
    assert barra_lojas.grupo_da_pasta("?", "INBOX/envio", "k1") == "envios"
    assert barra_lojas.grupo_da_pasta("x", None, "devoluçoes") == "devolucao"


async def test_toda_loja_tem_as_tres_caixas_com_os_numeros_do_grupo(db, cena, client, admin):
    h = await _historico(db, cena)
    await _na_pasta(db, cena, h["ml"], "problemas")
    await _na_pasta(db, cena, h["oliveira"], "envio")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)
    await _na_pasta(db, cena, ficha_cid, "Devoluçoes")

    simples = (await client.get(f"{EMAIL}/contagens")).json()
    assert "por_loja" not in simples and "grupos_na_barra" not in simples
    r = await _resposta(client)
    # Os números de cima: os MESMOS do caminho sem `por_loja` (uma consulta só, agrupada).
    assert {k: r[k] for k in simples} == simples
    assert [g["chave"] for g in r["grupos_na_barra"]] == GRUPOS
    assert [g["rotulo"] for g in r["grupos_na_barra"]] == [
        "Problemas",
        "Envios",
        "Devolução",
        "Informativo",
    ]
    # As pastas criadas aqui só contam (sem categoria): a ponte não liga o e-mail
    # delas; a "informativo" ainda não chegou à ponte (não existe).
    assert {g["chave"]: g["status"] for g in r["grupos_na_barra"]} == {
        "problemas": "so_contar",
        "envios": "so_contar",
        "devolucao": "so_contar",
        "informativo": "sem_pasta",
    }
    assert r["dias"] == mail_constantes.DIAS_DA_BARRA_DE_LOJAS
    lojas = {(i["integration_id"] or i["store_info_id"]): i for i in r["por_loja"]}
    # TODA loja, mesmo sem e-mail: as integrações e a ficha sem integração.
    integracoes = {
        str(cena.ml.id),
        str(cena.shopee.id),
        str(cena.amazon.id),
        str(cena.oliveira_ml.id),
    }
    assert integracoes | {ficha} <= set(lojas)
    for item in lojas.values():
        assert set(item["grupos"]) == set(GRUPOS)
    assert lojas[str(cena.ml.id)]["grupos"]["problemas"] == _vazio(emails=1, abertas=1, total=1)
    assert lojas[str(cena.ml.id)]["grupos"]["envios"] == _vazio()
    assert lojas[str(cena.ml.id)]["emails_30d"] == 1
    assert lojas[str(cena.oliveira_ml.id)]["grupos"]["envios"] == _vazio(
        emails=1, abertas=1, aguardando=1, total=1
    )
    assert lojas[ficha]["integration_id"] is None and lojas[ficha]["loja"] == "JLAS2"
    assert lojas[ficha]["plataforma"] == "ml"
    assert lojas[ficha]["grupos"]["devolucao"]["abertas"] == 1
    # A Shopee da Barbosa (só o chat): as três caixas vazias, sem e-mail nenhum.
    assert lojas[str(cena.shopee.id)]["grupos"] == {g: _vazio() for g in GRUPOS}
    assert lojas[str(cena.shopee.id)]["emails_30d"] == 0
    # O endereço da loja para a ponte (o cadastro está numa caixa com ponte).
    assert {lojas[k]["alias"] for k in (str(cena.ml.id), str(cena.oliveira_ml.id), ficha)} == {
        "na_ponte"
    }
    # Nada de endereço na resposta.
    assert "21max" not in str(r) and "16tr" not in str(r)

    # Fechou: sai das abertas, fica no total.
    c = await db.get(AtendimentoConversa, h["oliveira"])
    c.situacao, c.aguardando_resposta = "fechada", False
    await db.commit()
    item = (await _por_loja(client))[str(cena.oliveira_ml.id)]
    assert item["grupos"]["envios"] == _vazio(emails=1, total=1)


async def test_o_endereco_da_loja_para_a_ponte(db, cena, client, admin):
    """Sem e-mail no cadastro (`sem_cadastro`) × o e-mail fora das caixas com a
    ponte (`fora_da_caixa`) × numa caixa lida pela ponte (`na_ponte`)."""
    sem_ficha = await _integ(db, cena.dono, IntegrationPlatform.ML, "sem-ficha")
    fora = await _integ(db, cena.dono, IntegrationPlatform.SHOPEE, "fora-da-caixa")
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            platform="shopee",
            account_name="Fora",
            email="zz99",
            integration_id=fora.id,
        )
    )
    await db.commit()
    lojas = await _por_loja(client)
    assert lojas[str(sem_ficha.id)]["alias"] == "sem_cadastro"
    assert lojas[str(fora.id)]["alias"] == "fora_da_caixa"
    assert lojas[str(cena.ml.id)]["alias"] == "na_ponte"


async def test_o_grupo_e_a_pasta_do_recebido_mais_recente(db, cena, client, admin):
    h = await _historico(db, cena)
    await _na_pasta(db, cena, h["ml"], "envio")
    # Um e-mail RECEBIDO mais novo em Problemas: a conversa muda para Problemas
    # (num grupo só — os números somam sem repetir).
    await _email_na_conversa(db, cena, h["ml"], pasta="problemas", direcao="inbound", minutos=1)
    # O que NÓS mandamos (mais novo ainda, noutra pasta) não conta.
    await _email_na_conversa(db, cena, h["ml"], pasta="devoluçoes", direcao="sent", minutos=0)
    grupos = (await _por_loja(client))[str(cena.ml.id)]["grupos"]
    assert {g: n["total"] for g, n in grupos.items()} == {
        "problemas": 1,
        "envios": 0,
        "devolucao": 0,
        "informativo": 0,
    }

    def ids(grupo):
        return _ids(
            client, caixa="email", filtro="todas", integration_id=str(cena.ml.id), grupo_email=grupo
        )

    assert await ids("problemas") == {h["ml"]}
    assert await ids("envios") == set()
    assert await ids("devolucao") == set()


async def test_ficha_pelo_par_soma_na_loja_tambem_sem_acento_e_sem_prefixo(db, cena, client, admin):
    """A conversa que a ponte ligou só à FICHA (FK vazio), cuja integração
    existe pelo par nome + plataforma: na linha da loja da integração — os
    números e a lista. Desde 27/10/2026 o par ignora acento, espaços e o nome
    da plataforma na frente ("VR " × "Carrefour VR" em produção)."""
    await _historico(db, cena)
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)
    await _na_pasta(db, cena, ficha_cid, "problemas")
    params = {"caixa": "email", "filtro": "todas", "grupo_email": "problemas"}
    oliveira = {**params, "integration_id": str(cena.oliveira_ml.id)}
    assert ficha_cid not in await _ids(client, **oliveira)
    f = await db.get(StoreInfo, ficha)
    for nome in ("Oliveira-ML ", "Mercado Livre - óliveira-ml", "  OLIVEIRA-ml"):
        f.account_name = nome
        await db.commit()
        lojas = await _por_loja(client)
        assert ficha not in lojas, nome
        assert lojas[str(cena.oliveira_ml.id)]["grupos"]["problemas"]["abertas"] == 1, nome
        assert ficha_cid in await _ids(client, **oliveira), nome
    # A loja vizinha não ganha nada.
    assert ficha_cid not in await _ids(client, **params, integration_id=str(cena.ml.id))
    # Duas integrações com o mesmo par: ninguém adivinha — a ficha fica sozinha.
    f.account_name = "barbosa-ml"
    await db.commit()
    await _integ(db, cena.dono, cena.ml.platform, "Barbosa-ML")
    await db.commit()
    lojas = await _por_loja(client)
    assert lojas[ficha]["loja"] == "barbosa-ml"
    assert lojas[ficha]["grupos"]["problemas"]["abertas"] == 1
    assert lojas[str(cena.ml.id)]["grupos"]["problemas"]["abertas"] == 0


async def test_a_caixa_mostra_todo_email_da_pasta_uma_vez_so(
    db, cena, client, admin, auth_as, quem_le, make_user
):
    """O dono, 27/10: na caixa, TODO e-mail das pastas da loja — a linha
    informativa, a conversa, o que a ponte segurou como segurança (com o
    código à mostra: são os de postagem/devolução), o guardado de antes da
    pasta ser liberada — e o duplicado uma vez só."""
    from app.services.mail_atendimento import indice

    h = await _historico(db, cena)
    ml = {"para": ["21max@tuta.com"], "de": "avisos@mercadolivre.com.br"}
    # Antes de a pasta ser liberada: a ponte só contou (sem loja na meta).
    guardado = await entrar(db, cena.geral, pasta="problemas", **ml, assunto="Reclamação aberta")
    await rodar(db)
    assert (await meta_de(db, guardado)).motivo == "pasta_so_contar"
    await _pastas_ligadas(db, cena)
    linha = await entrar(
        db, cena.geral, pasta="envio", **ml, assunto="Pacote postado", message_id="<env1@ml>"
    )
    await rodar(db)
    # O mesmo Message-ID de novo na mesma pasta: duplicado do que já está na caixa.
    dup = await entrar(db, cena.geral, pasta="envio", **ml, message_id="<env1@ml>")
    # Segurado como segurança (o código da devolução) e o mesmo fora das pastas.
    codigo = await entrar(
        db,
        cena.geral,
        pasta="devoluçoes",
        **ml,
        assunto="Devolução",
        corpo="Use o código 482913 para entrar",
    )
    fora = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        **ml,
        assunto="Acesso",
        corpo="Use o código 771204 para entrar",
    )
    await rodar(db)
    # O duplicado cujo original NÃO está na barra (está na Entrada): aparece.
    na_entrada = await entrar(db, cena.geral, pasta="INBOX", **ml, message_id="<dup2@ml>")
    await rodar(db)
    dup_fora = await entrar(db, cena.geral, pasta="problemas", **ml, message_id="<dup2@ml>")
    await rodar(db)
    estados = {
        k: (await meta_de(db, v)).estado
        for k, v in locals().items()
        if k in ("linha", "dup", "codigo", "fora", "na_entrada", "dup_fora")
    }
    assert estados == {
        "linha": "informativo",
        "dup": "duplicado",
        "codigo": "seguranca",
        "fora": "seguranca",
        "na_entrada": "gravado",
        "dup_fora": "duplicado",
    }
    assert (await meta_de(db, codigo)).integration_id is None, "a ponte não grava a loja"
    # A conversa de e-mail do ML (já existente) também numa pasta da barra.
    await _na_pasta(db, cena, h["ml"], "problemas")
    await indice.rodar(db)  # a loja provável do que a ponte não ligou
    await db.commit()

    async def caixa(grupo, **loja):
        loja = loja or {"integration_id": str(cena.ml.id)}
        r = await client.get(f"{EMAIL}/lojas/emails", params={"grupo": grupo, **loja})
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["itens"]]

    assert await caixa("envios") == [str(linha)], "o duplicado da mesma pasta, uma vez só"
    assert await caixa("devolucao") == [str(codigo)]
    meta_ml = await db.scalar(
        select(MailMessageMeta.message_id).where(MailMessageMeta.conversa_id == h["ml"])
    )
    assert set(await caixa("problemas")) == {str(guardado), str(dup_fora), str(meta_ml)}
    assert await caixa("envios", integration_id=str(cena.shopee.id)) == []
    grupos = (await _por_loja(client))[str(cena.ml.id)]["grupos"]
    assert {g: n["emails"] for g, n in grupos.items()} == {
        "problemas": 3,
        "envios": 1,
        "devolucao": 1,
        "informativo": 0,
    }
    r = await _resposta(client)
    assert {g["chave"]: g["status"] for g in r["grupos_na_barra"]} == {
        **dict.fromkeys(GRUPOS, "liga"),
        "informativo": "sem_pasta",
    }
    # A linha da conversa diz qual conversa abrir.
    r = await client.get(
        f"{EMAIL}/lojas/emails", params={"grupo": "problemas", "integration_id": str(cena.ml.id)}
    )
    da_conversa = [i for i in r.json()["itens"] if i["id"] == str(meta_ml)][0]
    assert da_conversa["conversa_id"] == h["ml"]
    # O e-mail segurado, inteiro: o código à mostra (o link de acesso, não).
    d = await client.get(f"{EMAIL}/lojas/emails/{codigo}")
    assert d.status_code == 200 and "482913" in d.json()["texto"] and d.json()["seguranca"]
    # Fora das pastas da barra, a segurança continua escondida.
    assert (await client.get(f"{EMAIL}/lojas/emails/{fora}")).status_code == 404
    assert (await client.get(f"{EMAIL}/lojas/emails/{dup}")).status_code == 404
    for p in ({"grupo": "xx", "integration_id": str(cena.ml.id)}, {"grupo": "envios"}):
        assert (await client.get(f"{EMAIL}/lojas/emails", params=p)).status_code == 422

    # Quem só lê (não é admin) e vê a loja: o mesmo, pela barra.
    auth_as(quem_le)
    assert await caixa("devolucao") == [str(codigo)]
    d = await client.get(f"{EMAIL}/lojas/emails/{codigo}")
    assert d.status_code == 200 and "482913" in d.json()["texto"]

    # A equipe de OUTRA loja: nem o número nem o e-mail.
    auth_as(cena.dono)
    loja = await db.get(StoreInfo, cena.loja_oliveira_ml.id)
    loja.sales_team = 9
    membro = await make_user()
    membro.sales_teams = [9]
    await db.commit()
    auth_as(membro)
    assert str(cena.ml.id) not in await _por_loja(client)
    assert await caixa("devolucao") == []
    assert (await client.get(f"{EMAIL}/lojas/emails/{codigo}")).status_code == 404


async def test_lista_da_loja_por_grupo(db, cena, client, admin):
    h = await _historico(db, cena)
    await _na_pasta(db, cena, h["oliveira"], "problemas")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)
    await _na_pasta(db, cena, ficha_cid, "devoluçoes")

    def ids(**p):
        return _ids(client, caixa="email", filtro="todas", **p)

    oli = str(cena.oliveira_ml.id)
    assert await ids(integration_id=oli, grupo_email="problemas") == {h["oliveira"]}
    assert await ids(integration_id=oli, grupo_email="envios") == set()
    assert await ids(store_info_id=ficha, grupo_email="devolucao") == {ficha_cid}
    # Admin: a aba E-mail inteira, como antes (o tipo e a lista de todas as lojas).
    assert h["ml"] in await ids(integration_id=str(cena.ml.id))
    assert h["novo"] in await ids()
    # Grupo inválido, ou fora do e-mail: 422.
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "grupo_email": "venda"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "grupo_email_invalido"
    r = await client.get(f"{URL}/conversas", params={"grupo_email": "problemas"})
    assert r.status_code == 422
    # A Caixa (o chat) continua sem e-mail nenhum, com a loja escolhida.
    assert await _ids(client, integration_id=str(cena.ml.id), filtro="todas") == set()
    assert await _ids(client, integration_id=str(cena.shopee.id), filtro="todas") == {h["chat"]}


async def test_quem_nao_e_admin_ve_email_so_pela_barra(db, cena, client, quem_le, auth_as):
    h = await _historico(db, cena)
    await _na_pasta(db, cena, h["oliveira"], "problemas")
    ficha_cid, ficha = await _ficha_sem_integracao(db, cena)
    await _na_pasta(db, cena, ficha_cid, "envio")
    auth_as(quem_le)

    async def lista(**p):
        return await client.get(f"{URL}/conversas", params={"caixa": "email", **p})

    lojas = await _por_loja(client)
    assert {str(cena.oliveira_ml.id), ficha} <= set(lojas)
    r = await lista(integration_id=str(cena.oliveira_ml.id), grupo_email="problemas")
    assert r.status_code == 200 and [i["id"] for i in r.json()["itens"]] == [h["oliveira"]]
    r = await lista(store_info_id=ficha, grupo_email="envios", filtro="fechadas")
    assert r.status_code == 200, r.text
    # A conversa de e-mail aberta pela barra (os cartões) continua.
    assert (await client.get(f"{EMAIL}/conversas/{h['oliveira']}/emails")).status_code == 200
    # O resto da aba E-mail: 403 — todas as lojas, sem grupo (o tipo não basta), o site, a saúde.
    for p in (
        {},
        {"filtro": "aguardando"},
        {"grupo_email": "problemas"},
        {"integration_id": str(cena.ml.id)},
        {"integration_id": str(cena.ml.id), "tipo_email": "chamado"},
        {"tipo_chamado": "sac"},
    ):
        r = await lista(**p)
        assert r.status_code == 403, (p, r.text)
        assert r.json()["detail"]["code"] == SO_ADMIN
    for rota in ("saude", "filas", "informativos", "envios", "pastas", "regras"):
        r = await client.get(f"{EMAIL}/{rota}")
        assert r.status_code == 403, (rota, r.text)
    # O chat da Caixa não muda.
    assert await _ids(client, integration_id=str(cena.shopee.id), filtro="todas") == {h["chat"]}


async def test_admin_continua_com_a_aba_inteira(db, cena, client, admin):
    await _historico(db, cena)
    for rota in ("saude", "filas", "informativos", "envios", "pastas", "regras"):
        r = await client.get(f"{EMAIL}/{rota}")
        assert r.status_code == 200, (rota, r.text)
    r = await client.get(f"{URL}/conversas", params={"caixa": "email", "filtro": "todas"})
    assert r.status_code == 200 and r.json()["itens"]


async def test_loja_bloqueada_e_escopo_cortam_os_numeros(
    db, cena, client, make_user, auth_as, admin
):
    h = await _historico(db, cena)
    await _na_pasta(db, cena, h["ml"], "problemas")
    await _na_pasta(db, cena, h["oliveira"], "problemas")
    leitor = await make_user(role=UserRole.USER)
    await _salvar(client, auth_as, cena, leitor, lojas_bloqueadas=[f"i:{cena.ml.id}"])
    lojas = await _por_loja(client)
    # A loja continua na barra (é loja dele), mas sem o e-mail bloqueado.
    assert lojas[str(cena.ml.id)]["grupos"]["problemas"] == _vazio()
    assert lojas[str(cena.oliveira_ml.id)]["grupos"]["problemas"]["abertas"] == 1
    params = {"caixa": "email", "filtro": "todas", "grupo_email": "problemas"}
    assert await _ids(client, **params, integration_id=str(cena.ml.id)) == set()

    # A equipe da Barbosa (só a loja do ML dela): só a linha dela, sem fichas.
    auth_as(cena.dono)
    loja = await db.get(StoreInfo, cena.loja_ml.id)
    loja.sales_team = 7
    membro = await make_user()
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    assert set(await _por_loja(client)) == {str(cena.ml.id)}
