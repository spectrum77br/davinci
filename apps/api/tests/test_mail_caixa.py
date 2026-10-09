"""Aba E-mail › Caixas: as pastas como no Tuta e o SELO da loja de cada e-mail (09/10/2026).

Postgres local de verdade, o mesmo "mundo" da ponte (`tests/test_mail_ponte.cena`:
a caixa GERAL da empresa e a GOSLIN privada "só aliases de loja", o cadastro
das lojas com `store_info.email` só com a parte antes do @, a marca Uranyx
com sac@/atacado@/duvidas@).

O que não pode falhar:
  • a MESMA permissão da caixa dele: o dono ou um admin; quem não pode → 404;
  • o selo: o que a PONTE decidiu (meta) vale; sem meta, a loja PROVÁVEL
    (rotear, puro) — e nada disso grava meta, conversa ou pasta da ponte;
  • SEGURANÇA: o assunto nunca sai na lista (nem no JSON);
  • pasta e loja filtram, juntas, e a página segue sem repetir nem pular;
  • o índice não guarda nome de pasta, assunto, remetente nem texto;
  • a rota indexa só até o teto; o job termina; e-mail que muda é refeito.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    MailMailbox,
    MailMessage,
    StoreInfo,
    UserRole,
)
from app.models.mail_atendimento import MailCaixaIndice, MailFolder, MailMessageMeta
from app.security.cipher import decrypt_json, encrypt_json
from app.services.mail_atendimento import indice
from tests.test_mail_ponte import (
    AGORA,
    _caixa,
    entrar,
    meta_de,
    rodar,
)
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

URL = "/api/mail/mailboxes"
# Antes do corte da ponte (`ponte_desde` = ontem): a ponte nunca olha.
ANTIGO = AGORA - timedelta(days=3)


def _min(n: int):
    return ANTIGO - timedelta(minutes=n)


async def _pastas(client, caixa: MailMailbox, **params) -> dict:
    r = await client.get(f"{URL}/{caixa.id}/pastas", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def _lista(client, caixa: MailMailbox, **params) -> dict:
    r = await client.get(f"{URL}/{caixa.id}/lista", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _por_id(lista: dict) -> dict[str, dict]:
    return {item["id"]: item for item in lista["itens"]}


async def _contar(db, modelo) -> int:
    return int(await db.scalar(select(func.count()).select_from(modelo)) or 0)


# ── Permissão: a mesma da caixa dele ──────────────────────────────────────


async def test_dono_e_admin_veem_e_os_outros_recebem_404(db, cena, client, make_user, auth_as):
    dona = await make_user(role=UserRole.USER)
    caixa = _caixa(dona, "Da Dona — Tuta", "dona@tuta.com", [], "token-da-dona-0123456789abc")
    db.add(caixa)
    await db.commit()
    await entrar(db, caixa, pasta="INBOX", para=["dona@tuta.com"], recebido_em=_min(1))

    auth_as(dona)  # a dona (não é admin)
    assert (await _pastas(client, caixa))["pastas"][0]["total"] == 1
    assert len((await _lista(client, caixa))["itens"]) == 1
    # A dona não vê a caixa de outro dono: 404, como na Central.
    for rota in ("pastas", "lista"):
        r = await client.get(f"{URL}/{cena.geral.id}/{rota}")
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "mailbox_not_found")

    auth_as(cena.dono)  # admin, não é o dono
    assert len((await _lista(client, caixa))["itens"]) == 1

    outra = await make_user(role=UserRole.USER)
    auth_as(outra)
    for rota in ("pastas", "lista"):
        r = await client.get(f"{URL}/{caixa.id}/{rota}")
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "mailbox_not_found")
        r = await client.get(f"{URL}/{uuid4()}/{rota}")
        assert r.status_code == 404
    # Quem não pode não faz nem o índice da caixa andar.
    assert await _contar(db, MailCaixaIndice) == 1


async def test_parametro_estranho_e_422_sem_eco(db, cena, client, auth_as):
    auth_as(cena.dono)
    for params in (
        {"pasta": "INBOX"},
        {"loja": "loja-qualquer"},
        {"loja": "f:nao-e-uuid"},
        {"antes": "ontem"},
        {"antes": f"99999999999999999999_{uuid4()}"},
        {"limite": "0"},
        {"limite": "101"},
    ):
        r = await client.get(f"{URL}/{cena.geral.id}/lista", params=params)
        assert r.status_code == 422, params
        assert "INBOX" not in r.text and "loja-qualquer" not in r.text


# ── As pastas como no Tuta ────────────────────────────────────────────────


async def test_pastas_na_ordem_do_tuta_com_a_quantidade(db, cena, client, auth_as):
    for n, pasta in enumerate(
        [
            "Spam",
            "INBOX",
            "INBOX",
            "Sent",
            "Trash",
            "Drafts",
            "Archive",
            "*7buyers",
            "*avisos",
            "Ávila",
            "vendas ml",
            "vendas ml",
            "Bancos",
            "mensagens ml",
            "_pendentes",
            "2025",
        ]
    ):
        await entrar(db, cena.geral, pasta=pasta, recebido_em=_min(n))
    # Os agendados (v2: o tipo do Tuta) vêm logo depois dos Rascunhos, como no Tuta.
    await entrar(
        db,
        cena.geral,
        pasta="x",
        tuta={"folder_key": "ag", "folder_kind": "10", "folder_path": "Scheduled"},
        recebido_em=_min(20),
    )
    auth_as(cena.dono)
    corpo = await _pastas(client, cena.geral)
    nomes = [(p["nome"], p["tipo"], p["quantidade"]) for p in corpo["pastas"]]
    assert nomes == [
        ("Todas", "todas", 17),
        # `folderTypeToOrder` do Tuta: Entrada, Rascunhos, Agendados, Enviados,
        # Lixeira, Arquivo, Spam.
        ("Entrada", "sistema", 2),
        ("Rascunhos", "sistema", 1),
        ("Agendados", "sistema", 1),
        ("Enviados", "sistema", 1),
        ("Lixeira", "sistema", 1),
        ("Arquivo", "sistema", 1),
        ("Spam", "sistema", 1),
        # As pessoais na ordem do `localeCompare` do Tuta: sem acento nem
        # maiúscula, mas a pontuação ("*", "_") antes dos números e das letras —
        # o "*" do dono põe a pasta em cima.
        ("*7buyers", "pessoal", 1),
        ("*avisos", "pessoal", 1),
        ("_pendentes", "pessoal", 1),
        ("2025", "pessoal", 1),
        ("Ávila", "pessoal", 1),
        ("Bancos", "pessoal", 1),
        ("mensagens ml", "pessoal", 1),
        ("vendas ml", "pessoal", 2),
    ]
    assert corpo["faltam"] == 0
    ids = {p["nome"]: p["id"] for p in corpo["pastas"]}
    assert ids["Entrada"] == "s1" and ids["Enviados"] == "s2" and ids["Todas"] is None
    # Clicar na pasta filtra a lista (e a pasta vem com o nome em português).
    vendas = await _lista(client, cena.geral, pasta=ids["vendas ml"])
    assert len(vendas["itens"]) == 2
    assert {i["pasta"]["nome"] for i in vendas["itens"]} == {"vendas ml"}
    entrada = await _lista(client, cena.geral, pasta="s1")
    assert {(i["pasta"]["id"], i["pasta"]["nome"]) for i in entrada["itens"]} == {("s1", "Entrada")}


async def test_pastas_com_o_mesmo_nome_mostram_o_caminho(db, cena, client, auth_as):
    await entrar(db, cena.geral, pasta="INBOX/2025", recebido_em=_min(1))
    await entrar(db, cena.geral, pasta="Clientes/2025", recebido_em=_min(2))
    await entrar(db, cena.geral, pasta="Clientes/2025", recebido_em=_min(3))
    auth_as(cena.dono)
    pastas = (await _pastas(client, cena.geral))["pastas"]
    assert [(p["nome"], p["quantidade"]) for p in pastas] == [
        ("Todas", 3),
        ("Clientes/2025", 2),
        ("INBOX/2025", 1),
    ]


async def test_indice_nao_guarda_nome_de_pasta_nem_nada_do_email(db, cena, client, auth_as):
    mid = await entrar(
        db,
        cena.goslin,
        pasta="INBOX/Banco Itaú pessoal",
        para=["goslin@tuta.com"],
        de="gerente@itau.com.br",
        assunto="Extrato de setembro",
        corpo="Segue o extrato",
        recebido_em=_min(1),
    )
    outra = await entrar(
        db,
        cena.geral,
        pasta="INBOX/Banco Itaú pessoal",
        recebido_em=_min(2),
    )
    auth_as(cena.dono)
    await _pastas(client, cena.goslin)
    await _pastas(client, cena.geral)
    linhas = {r.message_id: r for r in (await db.scalars(select(MailCaixaIndice))).all()}
    um, dois = linhas[mid], linhas[outra]
    tudo = " ".join(str(v) for r in (um, dois) for v in vars(r).values()).lower()
    for pedaco in ("itau", "itaú", "banco", "pessoal", "extrato", "gerente", "goslin"):
        assert pedaco not in tudo
    # A chave opaca da MESMA pasta muda de uma caixa para outra.
    assert um.pasta_chave.startswith("p") and len(um.pasta_chave) == 32
    assert um.pasta_chave != dois.pasta_chave
    # Mas o dono vê o nome (decifrado na hora).
    nomes = [p["nome"] for p in (await _pastas(client, cena.goslin))["pastas"]]
    assert "Banco Itaú pessoal" in nomes


# ── O selo: a ponte × o provável ──────────────────────────────────────────


async def test_selo_da_loja_provavel_sem_ponte(db, cena, client, auth_as):
    jlas2 = await db.scalar(select(StoreInfo).where(StoreInfo.account_name == "JLAS2"))
    ml = await entrar(db, cena.geral, pasta="problema ml", recebido_em=_min(1))
    jl = await entrar(
        db, cena.geral, pasta="vendas ml", para=["16tr@tuta.com"], recebido_em=_min(2)
    )
    site = await entrar(
        db, cena.geral, pasta="INBOX", para=["sac@uranyx.com.br"], recebido_em=_min(3)
    )
    # Entrada para um alias de DUAS lojas (ML e Shopee) sem remetente oficial: sem loja.
    duas = await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(4))
    # O aviso OFICIAL do ML na Entrada: a loja do ML.
    oficial = await entrar(
        db, cena.geral, pasta="INBOX", de="avisos@mercadolivre.com.br", recebido_em=_min(5)
    )
    auth_as(cena.dono)
    itens = _por_id(await _lista(client, cena.geral))
    assert itens[str(ml)]["selo"] == {
        "tipo": "loja",
        "chave": f"f:{cena.loja_ml.id}",
        "rotulo": "Barbosa · ML",
        "plataforma": "ml",
        "loja": "Barbosa",
        "provavel": True,
    }
    assert itens[str(jl)]["selo"]["rotulo"] == "JLAS2 · ML"
    assert itens[str(jl)]["selo"]["chave"] == f"f:{jlas2.id}"
    assert itens[str(site)]["selo"] | {"chave": None} == {
        "tipo": "site",
        "chave": None,
        "rotulo": "Site Uranyx",
        "plataforma": "site",
        "loja": "Uranyx",
        "provavel": True,
    }
    assert itens[str(duas)]["selo"]["tipo"] == "sem_loja"
    assert itens[str(duas)]["selo"]["rotulo"] == "sem loja"
    assert itens[str(oficial)]["selo"]["chave"] == f"f:{cena.loja_ml.id}"
    # Nada disso passou pela ponte nem mexeu nela.
    assert {i["ponte"] for i in itens.values()} == {None}
    assert await _contar(db, MailMessageMeta) == 0
    assert await _contar(db, AtendimentoConversa) == 0
    assert await _contar(db, MailFolder) == 0


async def test_caixa_so_de_loja_o_resto_e_privado(db, cena, client, auth_as):
    privado = await entrar(
        db, cena.goslin, pasta="INBOX", para=["goslin@tuta.com"], recebido_em=_min(1)
    )
    loja = await entrar(
        db, cena.goslin, pasta="problema ml", para=["mia30@tuta.com"], recebido_em=_min(2)
    )
    auth_as(cena.dono)
    itens = _por_id(await _lista(client, cena.goslin))
    assert itens[str(privado)]["selo"]["tipo"] == "privado"
    assert itens[str(privado)]["selo"]["rotulo"] == "privado"
    assert itens[str(loja)]["selo"]["rotulo"] == "Oliveira · ML"


async def test_o_que_a_ponte_decidiu_vale_mais_que_o_provavel(db, cena, client, auth_as):
    # Depois do corte: a ponte processa (sem pedido = sem vínculo, na loja do ML).
    novo = await entrar(db, cena.geral, pasta="problema ml")
    # Entrada para o alias de duas lojas: a ponte deixa SEM LOJA.
    sem_loja = await entrar(db, cena.geral, pasta="INBOX")
    await rodar(db)
    assert (await meta_de(db, novo)).estado == "sem_vinculo"
    assert (await meta_de(db, sem_loja)).estado == "sem_loja"
    # Uma pessoa escolheu outra loja para um e-mail antigo (a meta diz Mike/Amazon).
    antigo = await entrar(db, cena.geral, pasta="problema ml", recebido_em=_min(1))
    mike = await db.scalar(select(StoreInfo).where(StoreInfo.account_name == "Mike"))
    db.add(
        MailMessageMeta(
            message_id=antigo,
            mailbox_id=cena.geral.id,
            estado="gravado",
            store_info_id=mike.id,
            integration_id=cena.amazon.id,
            plataforma="amazon",
        )
    )
    await db.commit()
    auth_as(cena.dono)
    itens = _por_id(await _lista(client, cena.geral))
    assert itens[str(novo)]["selo"]["rotulo"] == "Barbosa · ML"
    assert (itens[str(novo)]["selo"]["provavel"], itens[str(novo)]["ponte"]) == (
        False,
        "sem_vinculo",
    )
    assert itens[str(sem_loja)]["selo"]["tipo"] == "sem_loja"
    assert itens[str(sem_loja)]["selo"]["provavel"] is False
    assert itens[str(antigo)]["selo"]["rotulo"] == "Mike · Amazon"
    assert itens[str(antigo)]["selo"]["provavel"] is False
    # O índice guardou o PROVÁVEL (Barbosa); a tela mostra o da ponte.
    linha = await db.get(MailCaixaIndice, antigo)
    assert linha.store_info_id == cena.loja_ml.id
    # A meta muda depois (a pessoa escolhe de novo): a lista acompanha na hora.
    await db.execute(
        update(MailMessageMeta)
        .where(MailMessageMeta.message_id == antigo)
        .values(estado="sem_loja", store_info_id=None, integration_id=None)
    )
    await db.commit()
    itens = _por_id(await _lista(client, cena.geral))
    assert itens[str(antigo)]["selo"]["tipo"] == "sem_loja"


async def test_mesma_loja_pela_ficha_e_pela_integracao_e_uma_entrada_so(db, cena, client, auth_as):
    # O provável diz a FICHA (f:); a meta de uma escolha por integração diz i:.
    a = await entrar(db, cena.geral, pasta="problema ml", recebido_em=_min(1))
    b = await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(2))
    db.add(
        MailMessageMeta(
            message_id=b, mailbox_id=cena.geral.id, estado="gravado", integration_id=cena.ml.id
        )
    )
    await db.commit()
    auth_as(cena.dono)
    lojas = (await _pastas(client, cena.geral))["lojas"]
    barbosa = [x for x in lojas if x["rotulo"] == "Barbosa · ML"]
    assert len(barbosa) == 1 and barbosa[0]["quantidade"] == 2
    assert barbosa[0]["chave"] == f"f:{cena.loja_ml.id}"
    itens = await _lista(client, cena.geral, loja=barbosa[0]["chave"])
    assert {i["id"] for i in itens["itens"]} == {str(a), str(b)}
    assert {i["selo"]["chave"] for i in itens["itens"]} == {f"f:{cena.loja_ml.id}"}


# ── Segurança: o assunto nunca sai ────────────────────────────────────────

ASSUNTO_COD = "Seu código de verificação Zeta"


async def test_seguranca_sem_assunto_na_lista(db, cena, client, auth_as):
    # Antes do corte (sem ponte): a regra estrita daqui.
    antigo = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="no-reply@mercadolivre.com.br",
        assunto=ASSUNTO_COD,
        corpo="Use 482913 para entrar na sua conta.",
        recebido_em=_min(1),
    )
    # Depois do corte: a ponte diz `seguranca`.
    novo = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="no-reply@mercadolivre.com.br",
        assunto=ASSUNTO_COD,
        corpo="Use 551208 para entrar na sua conta.",
    )
    # Privado na Goslin E de segurança: esconde também.
    privado = await entrar(
        db,
        cena.goslin,
        pasta="INBOX",
        para=["goslin@tuta.com"],
        assunto=ASSUNTO_COD,
        corpo="Código 731604 para acessar.",
    )
    await rodar(db)
    assert (await meta_de(db, novo)).estado == "seguranca"
    assert (await meta_de(db, privado)).estado == "privado"
    auth_as(cena.dono)
    for caixa, mid, fonte in (
        (cena.geral, antigo, True),
        (cena.geral, novo, False),
        (cena.goslin, privado, True),
    ):
        r = await client.get(f"{URL}/{caixa.id}/lista")
        assert "Zeta" not in r.text and "482913" not in r.text and "731604" not in r.text
        item = _por_id(r.json())[str(mid)]
        assert (item["assunto"], item["assunto_oculto"]) == ("e-mail de acesso/código", True)
        assert item["selo"]["tipo"] == "seguranca" and item["selo"]["rotulo"] == "segurança"
        assert item["selo"]["provavel"] is fonte
    lojas = (await _pastas(client, cena.geral))["lojas"]
    assert {x["chave"]: x["quantidade"] for x in lojas}["seguranca"] == 2
    # Filtro "segurança": só eles.
    so = await _lista(client, cena.geral, loja="seguranca")
    assert {i["id"] for i in so["itens"]} == {str(antigo), str(novo)}


async def test_seguranca_com_erro_da_ponte_e_ignorado_por_pessoa(db, cena, client, auth_as):
    """Revisão de 09/10: a ponte FALHOU (erro, talvez antes do passo da segurança)
    e alguém tirou o e-mail da fila ("ignorar" → ignorado/por_pessoa). A ponte
    nunca olhou a segurança: a regra estrita do índice continua escondendo."""
    from app.services.mail_atendimento import ponte

    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="no-reply@mercadolivre.com.br",
        assunto="Seu codigo de acesso e 551177",
        corpo="Use 551177 para entrar na sua conta.",
    )
    await ponte.marcar_erro(db, mid, "OperationalError")
    await db.commit()
    auth_as(cena.dono)
    r = await client.get(f"{URL}/{cena.geral.id}/lista")
    assert "551177" not in r.text
    # A pessoa tira da fila pela rota de verdade do /atendimento.
    r = await client.post(f"/api/atendimento/email/emails/{mid}/ignorar")
    assert r.status_code == 200, r.text
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo) == ("ignorado", "por_pessoa")
    for params in ({}, {"loja": "seguranca"}, {"pasta": "s1"}):
        r = await client.get(f"{URL}/{cena.geral.id}/lista", params=params)
        assert "551177" not in r.text, params
    item = _por_id((await client.get(f"{URL}/{cena.geral.id}/lista")).json())[str(mid)]
    assert (item["assunto_oculto"], item["selo"]["tipo"]) == (True, "seguranca")


async def test_seguranca_so_vale_a_da_ponte_depois_do_passo_dela(db, cena, client, auth_as):
    """A lista de PERMISSÃO: só os estados/motivos que a ponte alcança DEPOIS da
    segurança deixam a decisão com ela; qualquer outro (até um motivo novo) esconde."""
    casos = {
        ("novo", None): True,
        ("erro", "OperationalError"): True,
        ("privado", "nao_e_de_loja"): True,
        ("ignorado", "pasta_so_contar"): True,
        ("ignorado", "pasta_nao_ler"): True,
        ("ignorado", "por_pessoa"): True,
        ("ignorado", None): True,
        ("ignorado", "motivo_que_alguem_criou"): True,
        ("gravado", None): False,
        ("sem_vinculo", None): False,
        ("sem_loja", "nenhuma_loja"): False,
        ("resumo", None): False,
        ("duplicado", None): False,
        ("interno", None): False,
        ("ignorado", "aviso_do_tuta"): False,
        ("ignorado", "alias_interno"): False,
        ("ignorado", "enviado_sem_conversa"): False,
        ("ignorado", "devolucao_sem_conversa"): False,
    }
    ids = {}
    for n, (estado, motivo) in enumerate(casos):
        mid = await entrar(
            db,
            cena.geral,
            pasta="INBOX",
            assunto=f"Seu codigo de verificacao Kappa{n}",
            corpo="Use 482913 para entrar na sua conta.",
            recebido_em=_min(n),
        )
        db.add(
            MailMessageMeta(message_id=mid, mailbox_id=cena.geral.id, estado=estado, motivo=motivo)
        )
        ids[(estado, motivo)] = mid
    await db.commit()
    auth_as(cena.dono)
    itens = _por_id(await _lista(client, cena.geral))
    for caso, esconde in casos.items():
        item = itens[str(ids[caso])]
        assert item["assunto_oculto"] is esconde, caso
        assert ("Kappa" in item["assunto"]) is not esconde, caso


async def test_ponte_que_ja_olhou_a_seguranca_vale(db, cena, client, auth_as):
    # O cliente que fala de senha na caixa de site: a ponte leva para a equipe
    # (regra de pessoa). A regra estrita daqui diria "segurança" — vale a ponte.
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["sac@uranyx.com.br"],
        de="cliente@gmail.com",
        assunto="Esqueci minha senha do site",
        corpo="Não consigo entrar, esqueci minha senha. Podem ajudar?",
    )
    await rodar(db)
    estado = (await meta_de(db, mid)).estado
    assert estado in ("gravado", "sem_vinculo")
    auth_as(cena.dono)
    item = _por_id(await _lista(client, cena.geral))[str(mid)]
    assert item["selo"]["rotulo"] == "Site Uranyx"
    assert item["assunto"] == "Esqueci minha senha do site" and item["assunto_oculto"] is False


# ── Filtro por pasta e loja, juntos; paginação ────────────────────────────


async def test_filtro_por_pasta_e_loja_combinados(db, cena, client, auth_as):
    ids: dict[str, list[UUID]] = {"ml_problema": [], "ml_vendas": [], "jl_vendas": [], "inbox": []}
    for n in range(3):
        ids["ml_problema"].append(
            await entrar(db, cena.geral, pasta="problema ml", recebido_em=_min(10 + n))
        )
        ids["ml_vendas"].append(
            await entrar(db, cena.geral, pasta="vendas ml", recebido_em=_min(20 + n))
        )
    ids["jl_vendas"].append(
        await entrar(
            db, cena.geral, pasta="vendas ml", para=["16tr@tuta.com"], recebido_em=_min(30)
        )
    )
    ids["inbox"].append(await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(31)))
    auth_as(cena.dono)
    corpo = await _pastas(client, cena.geral)
    pasta = {p["nome"]: p["id"] for p in corpo["pastas"]}
    lojas = {x["rotulo"]: x for x in corpo["lojas"]}
    assert [x["rotulo"] for x in corpo["lojas"]] == ["Barbosa · ML", "JLAS2 · ML", "sem loja"]
    assert (lojas["Barbosa · ML"]["quantidade"], lojas["JLAS2 · ML"]["quantidade"]) == (6, 1)
    barbosa = lojas["Barbosa · ML"]["chave"]

    # Só a loja.
    so_loja = await _lista(client, cena.geral, loja=barbosa)
    assert {UUID(i["id"]) for i in so_loja["itens"]} == {*ids["ml_problema"], *ids["ml_vendas"]}
    # A loja E a pasta.
    junto = await _lista(client, cena.geral, loja=barbosa, pasta=pasta["vendas ml"])
    assert {UUID(i["id"]) for i in junto["itens"]} == set(ids["ml_vendas"])
    # "sem loja".
    sem = await _lista(client, cena.geral, loja="sem_loja")
    assert {UUID(i["id"]) for i in sem["itens"]} == set(ids["inbox"])
    # As contagens cruzadas: pastas só da loja; lojas só da pasta (total = sem filtro).
    da_loja = await _pastas(client, cena.geral, loja=barbosa)
    q = {p["nome"]: (p["quantidade"], p["total"]) for p in da_loja["pastas"]}
    assert q["Todas"] == (6, 8) and q["vendas ml"] == (3, 4) and q["Entrada"] == (0, 1)
    da_pasta = await _pastas(client, cena.geral, pasta=pasta["vendas ml"])
    q = {x["rotulo"]: (x["quantidade"], x["total"]) for x in da_pasta["lojas"]}
    assert q == {"Barbosa · ML": (3, 6), "JLAS2 · ML": (1, 1), "sem loja": (0, 1)}


async def test_paginacao_sem_repetir_nem_pular(db, cena, client, auth_as):
    # 120 e-mails; de 3 em 3 com o MESMO horário (o desempate é o id).
    todos = []
    for n in range(120):
        todos.append(await entrar(db, cena.geral, pasta="problema ml", recebido_em=_min(n // 3)))
    auth_as(cena.dono)
    vistos: list[str] = []
    antes = None
    paginas = 0
    while True:
        params = {"limite": 50, **({"antes": antes} if antes else {})}
        pagina = await _lista(client, cena.geral, **params)
        vistos += [i["id"] for i in pagina["itens"]]
        paginas += 1
        antes = pagina["proximo"]
        if antes is None:
            break
    assert paginas == 3
    assert len(vistos) == len(set(vistos)) == 120
    assert set(vistos) == {str(m) for m in todos}
    # O mais novo primeiro.
    recebidos = {
        str(m.id): (m.received_at, m.id) for m in (await db.scalars(select(MailMessage))).all()
    }
    chaves = [recebidos[v] for v in vistos]
    assert chaves == sorted(chaves, reverse=True)


# ── O índice: a rota até o teto, o job, e o que muda ──────────────────────


async def test_rota_indexa_ate_o_teto_e_o_job_termina(db, cena, client, auth_as, monkeypatch):
    monkeypatch.setattr(indice, "ROTA_MAXIMO", 5)
    monkeypatch.setattr(indice, "LOTE", 4)
    for n in range(12):
        await entrar(db, cena.geral, pasta="problema ml", recebido_em=_min(n))
    auth_as(cena.dono)
    primeira = await _lista(client, cena.geral, limite=3)
    assert primeira["faltam"] == 7
    # O mais novo primeiro: a 1ª página de "Todas" já está certa.
    novos = (
        await db.scalars(
            select(MailMessage.id)
            .where(MailMessage.mailbox_id == cena.geral.id)
            .order_by(MailMessage.received_at.desc())
            .limit(3)
        )
    ).all()
    assert [i["id"] for i in primeira["itens"]] == [str(m) for m in novos]
    resumo = await indice.rodar(db)
    assert resumo["indexados"] == 7
    assert (await indice.rodar(db))["indexados"] == 0
    corpo = await _pastas(client, cena.geral)
    assert (corpo["faltam"], corpo["pastas"][0]["total"]) == (0, 12)


async def test_job_respeita_o_teto_da_volta(db, cena, monkeypatch):
    monkeypatch.setattr(indice, "LOTE", 3)
    for n in range(5):
        await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(n))
        await entrar(db, cena.goslin, pasta="INBOX", para=["mia30@tuta.com"], recebido_em=_min(n))
    assert (await indice.rodar(db, maximo=4))["indexados"] == 4
    assert (await indice.rodar(db))["indexados"] == 6
    assert await _contar(db, MailCaixaIndice) == 10
    # Nada da ponte foi tocado.
    assert await _contar(db, MailMessageMeta) == 0
    assert await _contar(db, AtendimentoMensagem) == 0


async def test_job_reveza_as_caixas(db, cena, monkeypatch):
    """Uma fila grande numa caixa não deixa a outra esperando: um lote de cada por vez."""
    monkeypatch.setattr(indice, "LOTE", 3)
    primeira, segunda = sorted([cena.geral, cena.goslin], key=lambda c: c.id)
    for n in range(12):
        para = ["21max@tuta.com"] if primeira is cena.geral else ["mia30@tuta.com"]
        await entrar(db, primeira, pasta="INBOX", para=para, recebido_em=_min(n))
    para = ["21max@tuta.com"] if segunda is cena.geral else ["mia30@tuta.com"]
    for n in range(2):
        await entrar(db, segunda, pasta="INBOX", para=para, recebido_em=_min(n))
    assert await indice.rodar(db, maximo=6) == {"indexados": 6, "caixas": 2}

    async def na_caixa(caixa) -> int:
        return int(
            await db.scalar(
                select(func.count())
                .select_from(MailCaixaIndice)
                .where(MailCaixaIndice.mailbox_id == caixa.id)
            )
            or 0
        )

    assert (await na_caixa(primeira), await na_caixa(segunda)) == (4, 2)


async def test_job_espera_a_trava_da_rota_em_vez_de_largar_a_caixa(db, cena, monkeypatch):
    """A rota segurando a trava da caixa: o job tenta de novo (não desiste da fila)."""
    monkeypatch.setattr(indice, "TRAVA_ESPERA", 0.01)
    for n in range(4):
        await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(n))
    original = indice.pegar_trava
    recusas = {"n": 0}

    async def ocupada_duas_vezes(session, mailbox_id):
        if mailbox_id == cena.geral.id and recusas["n"] < 2:
            recusas["n"] += 1
            return False
        return await original(session, mailbox_id)

    monkeypatch.setattr(indice, "pegar_trava", ocupada_duas_vezes)
    assert (await indice.rodar(db))["indexados"] == 4
    assert recusas["n"] == 2
    # Ocupada a volta inteira: desiste depois de TRAVA_TENTATIVAS, sem travar o job.
    await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(9))

    async def sempre_ocupada(session, mailbox_id):
        return False

    monkeypatch.setattr(indice, "pegar_trava", sempre_ocupada)
    assert (await indice.rodar(db))["indexados"] == 0


async def test_job_confere_o_prazo_a_cada_email_e_descansa(db, cena, monkeypatch):
    """O prazo da volta vale DENTRO do lote (um lote de e-mails enormes não
    passa do timeout do cron), e o job descansa o tempo que gastou em cada e-mail."""
    monkeypatch.setattr(indice, "LOTE", 50)
    for n in range(6):
        await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(n))
    relogio = {"t": 0.0}
    monkeypatch.setattr(indice, "_relogio", lambda: relogio["t"])
    original = indice.calcular

    def calcular_e_andar(message, base):
        relogio["t"] += 10.0  # cada e-mail "custa" 10 s
        return original(message, base)

    monkeypatch.setattr(indice, "calcular", calcular_e_andar)
    pausas: list[float] = []

    async def anotar(segundos):
        pausas.append(segundos)

    monkeypatch.setattr(indice, "_descansar", anotar)
    # Prazo de 25 s: o 3º e-mail passa do prazo; os 3 calculados são gravados.
    assert (await indice.rodar(db, segundos=25.0))["indexados"] == 3
    assert await _contar(db, MailCaixaIndice) == 3
    # Descansou depois de cada e-mail (pausa = o tempo gasto × JOB_PAUSA > 0).
    assert len(pausas) == 3 and all(p > 0 for p in pausas)


async def test_regra_de_seguranca_le_so_o_comeco_de_um_email_enorme(db, cena, monkeypatch):
    """Um e-mail de 2 MB não para o event loop: a regra lê no máximo TETO_TEXTO."""
    from app.services.mail_atendimento import codigos

    lidos: list[int] = []
    original = codigos.e_de_seguranca

    def medir(*textos, **k):
        lidos.append(sum(len(t or "") for t in textos))
        return original(*textos, **k)

    monkeypatch.setattr(codigos, "e_de_seguranca", medir)
    grande = "Oferta da semana, veja os produtos.\n" * 60000  # ~2 MB
    mid = await entrar(db, cena.geral, pasta="INBOX", corpo=grande, recebido_em=_min(1))
    html = "<html><body>" + "<p>Produto em oferta por R$ 10,90</p>" * 50000 + "</body></html>"
    outro = await entrar(db, cena.geral, pasta="INBOX", corpo=html, recebido_em=_min(2))
    # O código logo no começo continua pegando.
    codigo = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        assunto="Aviso",
        corpo="Seu código de acesso é 731604.\n" + grande,
        recebido_em=_min(3),
    )
    assert (await indice.rodar(db))["indexados"] == 3
    assert lidos and max(lidos) <= indice.TETO_TEXTO + 1000
    assert (await db.get(MailCaixaIndice, mid)).seguranca is False
    assert (await db.get(MailCaixaIndice, outro)).seguranca is False
    assert (await db.get(MailCaixaIndice, codigo)).seguranca is True


def test_cron_do_indice_fora_do_segundo_da_ponte():
    """O job começa no segundo 30 (a ponte e os outros crons do worker, no 0) e a
    volta cabe antes do minuto seguinte, com folga até o timeout do cron."""
    from app import worker

    crons = {c.coroutine.__name__: c for c in worker.WorkerSettings.cron_jobs}
    indice_cron, ponte_cron = crons["mail_caixa_indice"], crons["mail_ponte"]
    assert indice_cron.second == {30} and ponte_cron.second == 0
    assert indice_cron.minute is None  # a cada minuto
    assert indice.JOB_SEGUNDOS < 30 and indice.JOB_SEGUNDOS * 2 < indice_cron.timeout_s


async def test_job_do_worker_com_a_trava(db, cena, monkeypatch):
    from contextlib import asynccontextmanager

    import app.redis_client as rc
    from app import worker

    @asynccontextmanager
    async def _sessao():
        yield db

    class _Trava:
        def __init__(self, livre: bool):
            self.livre = livre
            self.apagou = False

        async def set(self, *_a, **_k):
            return self.livre

        async def delete(self, *_a, **_k):
            self.apagou = True
            return 1

    monkeypatch.setattr(worker, "session_scope", _sessao)
    await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(1))
    # Outra volta segurando a trava: esta pula, sem tocar em nada.
    ocupada = _Trava(livre=False)
    monkeypatch.setattr(rc, "redis", ocupada)
    assert await worker.mail_caixa_indice({}) == {"pulado": True}
    assert await _contar(db, MailCaixaIndice) == 0
    livre = _Trava(livre=True)
    monkeypatch.setattr(rc, "redis", livre)
    assert await worker.mail_caixa_indice({}) == {"indexados": 1, "caixas": 1}
    assert livre.apagou and await _contar(db, MailCaixaIndice) == 1


async def test_email_movido_e_cadastro_novo_refazem_a_linha(db, cena, client, auth_as):
    # hans21@ é alias da caixa sem loja no cadastro (ainda).
    movido = await entrar(
        db, cena.geral, pasta="INBOX", para=["hans21@tuta.com"], recebido_em=_min(1)
    )
    parado = await entrar(
        db, cena.geral, pasta="vendas shopee", para=["hans21@tuta.com"], recebido_em=_min(2)
    )
    auth_as(cena.dono)
    itens = _por_id(await _lista(client, cena.geral))
    assert (itens[str(movido)]["pasta"]["nome"], itens[str(movido)]["selo"]["tipo"]) == (
        "Entrada",
        "sem_loja",
    )
    assert itens[str(parado)]["selo"]["tipo"] == "sem_loja"
    # O conector v2 move o e-mail (regrava a pasta no conteúdo cifrado): a
    # ROTA refaz na hora (a pasta da lista não pode ficar errada).
    message = await db.get(MailMessage, movido)
    conteudo = decrypt_json(message.content_enc)
    conteudo["folder"] = "problema shopee"
    message.content_enc = encrypt_json(conteudo)
    await db.commit()
    corpo = await _pastas(client, cena.geral)
    assert {p["nome"]: p["quantidade"] for p in corpo["pastas"]} == {
        "Todas": 2,
        "problema shopee": 1,
        "vendas shopee": 1,
    }
    item = _por_id(await _lista(client, cena.geral))[str(movido)]
    assert item["pasta"]["nome"] == "problema shopee"
    linha = await db.get(MailCaixaIndice, movido)
    await db.refresh(linha)
    assert linha.pasta_chave.startswith("p") and linha.pasta_tipo == "0"
    # O cadastro ganha a loja deste alias (Shopee): a base muda. A lista
    # continua com o provável de antes (nada some) até o job refazer.
    db.add(
        StoreInfo(
            user_id=cena.dono.id,
            platform="shopee",
            account_name="Nova",
            email="hans21",
            integration_id=None,
        )
    )
    await db.commit()
    lista = await _lista(client, cena.geral)
    assert lista["faltam"] == 0
    assert _por_id(lista)[str(parado)]["selo"]["tipo"] == "sem_loja"
    assert (await indice.rodar(db))["indexados"] == 2
    itens = _por_id(await _lista(client, cena.geral))
    assert itens[str(parado)]["selo"]["rotulo"] == "Nova · Shopee"
    assert itens[str(movido)]["selo"]["rotulo"] == "Nova · Shopee"


async def test_escolha_de_pessoa_na_pasta_vale_no_provavel(db, cena, client, auth_as):
    mid = await entrar(
        db, cena.geral, pasta="clientes", para=["mike14@tuta.com"], recebido_em=_min(1)
    )
    auth_as(cena.dono)
    item = _por_id(await _lista(client, cena.geral))[str(mid)]
    # "clientes" não tem plataforma; mike14 é de uma loja só (Amazon) → a loja dela.
    assert item["selo"]["rotulo"] == "Mike · Amazon"
    # Uma pessoa disse que a pasta é do ML: o alias não é de loja do ML → sem loja.
    db.add(
        MailFolder(
            mailbox_id=cena.geral.id,
            chave="clientes",
            nome="clientes",
            tipo_tuta="0",
            plataforma_manual="ml",
        )
    )
    await db.commit()
    # A escolha muda a base: o job refaz (a rota não decifra tudo de novo).
    assert (await indice.rodar(db))["indexados"] == 1
    item = _por_id(await _lista(client, cena.geral))[str(mid)]
    assert item["selo"]["tipo"] == "sem_loja"
    # A pasta que a PONTE cria sozinha (sem escolha de pessoa) não muda a base.
    db.add(MailFolder(mailbox_id=cena.geral.id, chave="INBOX", nome="INBOX", tipo_tuta="1"))
    await db.commit()
    assert (await indice.rodar(db))["indexados"] == 0


async def test_email_ilegivel_fica_a_parte_e_escondido(db, cena, client, auth_as):
    mid = await entrar(db, cena.geral, pasta="INBOX", recebido_em=_min(1))
    await db.execute(update(MailMessage).where(MailMessage.id == mid).values(content_enc=b"0" * 40))
    await db.commit()
    auth_as(cena.dono)
    corpo = await _pastas(client, cena.geral)
    assert [p["tipo"] for p in corpo["pastas"]] == ["todas", "ilegivel"]
    item = _por_id(await _lista(client, cena.geral))[str(mid)]
    assert item["assunto_oculto"] is True and item["selo"]["tipo"] == "seguranca"
    assert item["de"] is None


# ── As peças puras ────────────────────────────────────────────────────────


def test_chave_e_nome_da_pasta():
    from app.services.mail_atendimento import pastas

    caixa = uuid4()
    entrada = pastas.do_conteudo({"folder": "INBOX"})
    assert (indice.chave_da_pasta(caixa, entrada), indice.nome_da_pasta(entrada)) == (
        "s1",
        "Entrada",
    )
    sent = pastas.do_conteudo({"folder": "Sent"})
    assert indice.chave_da_pasta(caixa, sent) == "s2"
    pessoal = pastas.do_conteudo({"folder": "INBOX/vendas ml"})
    chave = indice.chave_da_pasta(caixa, pessoal)
    assert chave.startswith("p") and len(chave) == 32 and "vendas" not in chave
    assert indice.chave_da_pasta(caixa, pessoal) == chave
    assert indice.chave_da_pasta(uuid4(), pessoal) != chave
    assert indice.nome_da_pasta(pessoal) == "vendas ml"
    # v2: o tipo do Tuta manda (a pasta renomeada continua a mesma).
    v2 = pastas.do_conteudo(
        {"folder": "x", "tuta": {"folder_key": "abc", "folder_kind": "3", "folder_path": "Lixo"}}
    )
    assert indice.chave_da_pasta(caixa, v2) == "s3"
