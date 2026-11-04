# ruff: noqa: F401, F811 — fixtures compartilhadas da ponte e das rotas
"""Quem SÓ VÊ a aba E-mail, nas lojas dele (04/11/2026).

O Eduardo, do marrocos: "dxa ele ver tdo na aba de email dele la as lojas sege so
as dele". Quem não é admin e tem `atendimento_email_aba` view lê tudo o que a aba
mostra. O que não pode falhar, rota por rota:

  • o e-mail ligado às lojas dele (a integração, a ficha sozinha ou — sem a meta
    dizer — o índice) aparece; o sem loja nenhuma também;
  • o de loja que não é dele não aparece em lugar nenhum: contagem, lista, detalhe,
    anexo, pasta informativa, revisar envio, pastas, saúde e busca;
  • toda ação continua 403 para ele, sem mudar nada;
  • quem não tem a permissão não muda (os mesmos 403) e a permissão antiga e a
    `atendimento_email_leitura` não ganham as filas; o admin vê tudo como antes;
  • a Central (/api/mail/*) continua fechada para ele.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import (
    AtendimentoConversa,
    MailAttachment,
    MailFolder,
    MailMessage,
    MailOutbox,
    StoreInfo,
    UserRole,
)
from app.models.mail_admin import MailAcesso
from app.models.mail_atendimento import MailCaixaIndice, MailMessageMeta, MailOutboxMeta
from app.security.cipher import encrypt_bytes, encrypt_json
from app.services.atendimento import acesso, escopo
from tests.lojas_da_pessoa import dar_lojas
from tests.test_atendimento_separa_email import URL
from tests.test_atendimento_separa_email_ui import _historico
from tests.test_mail_ponte import AGORA, _config, _loja, cena

pytestmark = pytest.mark.lojas_por_pessoa  # a regra de verdade, sem o legado do conftest

EMAIL = f"{URL}/email"
PERMISSAO = {"atendimento_email_aba": {"view": True}}
MEU, OUTRO, SEM = "meu", "outro", "sem"


async def _email(
    db,
    cena,
    estado: str,
    lado: str,
    *,
    integ=None,
    ficha=None,
    indice=None,
    conversa=None,
    pasta=None,
    suspeito=False,
    finalidade=None,
    motivo=None,
    assunto="Assunto",
    minutos=5,
    sugestoes=None,
) -> SimpleNamespace:
    """Um e-mail com a meta (e o anexo). `indice`: (integração, ficha) do índice leve."""
    msg = MailMessage(
        id=uuid4(),
        mailbox_id=cena.geral.id,
        source_id=f"tuta:L{uuid4().hex[:8]}/E{uuid4().hex[:8]}",
        direction="inbound",
        received_at=AGORA - timedelta(minutes=minutos),
        content_enc=encrypt_json(
            {"subject": assunto, "text": "Corpo", "from_address": "a@b.invalid", "to": []}
        ),
        attachment_count=1,
    )
    db.add(msg)
    await db.flush()
    anexo = MailAttachment(
        message_id=msg.id,
        metadata_enc=encrypt_json({"filename": "a.txt", "content_type": "text/plain"}),
        content_enc=encrypt_bytes(b"anexo"),
        size=5,
    )
    db.add(anexo)
    db.add(
        MailMessageMeta(
            message_id=msg.id,
            mailbox_id=cena.geral.id,
            estado=estado,
            integration_id=getattr(integ, "id", None),
            store_info_id=getattr(ficha, "id", None),
            folder_id=getattr(pasta, "id", None),
            conversa_id=getattr(conversa, "id", None),
            suspeito=suspeito,
            finalidade=finalidade,
            motivo=motivo,
            tuta_id="LISTA/ELEMENTO",
            # O sem loja de verdade traz a sugestão de MARCA, sem loja: não esconde.
            sugestoes=sugestoes
            if sugestoes is not None
            else [
                {
                    "store_info_id": str(cena.loja_oliveira_ml.id) if integ or ficha else None,
                    "nome": "Oliveira",
                    "plataforma": "ml",
                    "integration_id": str(cena.oliveira_ml.id) if integ or ficha else None,
                    "sugestao": True,
                }
            ],
            pedidos_citados=[{"pedido": "123", "existe": True, "plataforma": "ml"}],
        )
    )
    if indice is not None:
        db.add(
            MailCaixaIndice(
                message_id=msg.id,
                mailbox_id=cena.geral.id,
                recebido_em=msg.received_at,
                pasta_chave="s1",
                pasta_tipo="1",
                selo="loja",
                integration_id=getattr(indice[0], "id", None),
                store_info_id=getattr(indice[1], "id", None),
                fonte_em=msg.received_at,
                base="x",
                regras_versao=1,
            )
        )
    await db.flush()
    return SimpleNamespace(id=str(msg.id), uuid=msg.id, anexo=str(anexo.id), lado=lado)


async def _conversa_de_email(db, integ, *, plataforma="ml", ficha=None):
    c = AtendimentoConversa(
        integration_id=getattr(integ, "id", None),
        plataforma=plataforma,
        canal="email",
        externo_id=f"email-{uuid4().hex[:8]}",
        dados={"fonte": "tuta", "mail": {"store_info_id": str(ficha.id)} if ficha else {}},
    )
    db.add(c)
    await db.flush()
    return c


@pytest.fixture
async def mundo(db, cena, make_user, auth_as) -> SimpleNamespace:
    """Duas lojas dele (a Barbosa ML, com integração; a JLAS2, só a ficha), duas de
    outra pessoa (a Oliveira ML; a Temu só com a ficha) e e-mails em toda fila."""
    dono = cena.dono
    jlas2 = await db.scalar(select(StoreInfo).where(StoreInfo.account_name == "JLAS2"))
    outra_ficha = await _loja(db, dono, "temu", "outra-temu", None, "Outra Temu")
    ml, oli, loja_ml, loja_oli = cena.ml, cena.oliveira_ml, cena.loja_ml, cena.loja_oliveira_ml

    avisos = MailFolder(
        mailbox_id=cena.geral.id,
        chave="avisos",
        nome="avisos",
        caminho="avisos",
        tipo_tuta="0",
        ler="corpo",
        categoria="informativo",
    )
    so_dos_outros = MailFolder(
        mailbox_id=cena.geral.id,
        chave="so-dos-outros",
        nome="so-dos-outros",
        caminho="so-dos-outros",
        tipo_tuta="0",
        ler="corpo",
        categoria="informativo",
    )
    guardar = MailFolder(
        mailbox_id=cena.geral.id,
        chave="sem-categoria",
        nome="sem-categoria",
        caminho="sem-categoria",
        tipo_tuta="0",
        ler="so_contar",
    )
    db.add_all([avisos, so_dos_outros, guardar])
    await db.flush()

    conversa_minha = await _conversa_de_email(db, ml, ficha=loja_ml)
    conversa_ficha = await _conversa_de_email(db, None, ficha=jlas2)
    conversa_dos_outros = await _conversa_de_email(db, oli, ficha=loja_oli)
    conversa_do_site = await _conversa_de_email(db, None, plataforma="site")

    filas: dict[str, list[SimpleNamespace]] = {
        "sem_loja": [
            await _email(db, cena, "sem_loja", SEM, motivo="alias_sem_cadastro"),
            # A meta não diz, o índice diz: conta a loja do índice.
            await _email(db, cena, "sem_loja", MEU, indice=(ml, loja_ml)),
            await _email(db, cena, "sem_loja", MEU, indice=(None, jlas2)),
            await _email(db, cena, "sem_loja", OUTRO, indice=(oli, loja_oli)),
            await _email(db, cena, "sem_loja", OUTRO, indice=(None, outra_ficha)),
        ],
        "sem_vinculo": [
            await _email(
                db,
                cena,
                "sem_vinculo",
                MEU,
                integ=ml,
                ficha=loja_ml,
                conversa=conversa_minha,
                finalidade="mensagens",
            ),
            await _email(
                db,
                cena,
                "sem_vinculo",
                MEU,
                ficha=jlas2,
                conversa=conversa_ficha,
                finalidade="mensagens",
            ),
            await _email(
                db,
                cena,
                "sem_vinculo",
                OUTRO,
                integ=oli,
                ficha=loja_oli,
                conversa=conversa_dos_outros,
                finalidade="mensagens",
            ),
        ],
        "resumo": [
            await _email(db, cena, "resumo", MEU, ficha=jlas2),
            await _email(db, cena, "resumo", OUTRO, ficha=outra_ficha),
        ],
        "erro": [
            await _email(db, cena, "erro", SEM),
            await _email(db, cena, "erro", MEU, integ=ml, ficha=loja_ml),
            await _email(db, cena, "erro", OUTRO, integ=oli, ficha=loja_oli),
        ],
        "suspeito": [
            await _email(db, cena, "gravado", MEU, integ=ml, suspeito=True),
            await _email(db, cena, "gravado", OUTRO, integ=oli, suspeito=True),
            # Só a conversa diz a loja: é da loja dela.
            await _email(db, cena, "gravado", OUTRO, conversa=conversa_dos_outros, suspeito=True),
            # O chamado do site não tem loja: aparece, sem o link da conversa.
            await _email(db, cena, "gravado", SEM, conversa=conversa_do_site, suspeito=True),
        ],
        "informativo": [
            await _email(db, cena, "informativo", SEM, pasta=avisos),
            await _email(db, cena, "informativo", MEU, integ=ml, ficha=loja_ml, pasta=avisos),
            await _email(db, cena, "informativo", MEU, ficha=jlas2, pasta=avisos),
            await _email(db, cena, "informativo", OUTRO, integ=oli, ficha=loja_oli, pasta=avisos),
            await _email(db, cena, "informativo", OUTRO, ficha=outra_ficha, pasta=avisos),
            await _email(
                db, cena, "informativo", OUTRO, integ=oli, ficha=loja_oli, pasta=so_dos_outros
            ),
        ],
    }
    # Os guardados da pasta sem categoria (a conta de GET /pastas).
    for lado, integ, ficha in ((MEU, ml, loja_ml), (OUTRO, oli, loja_oli), (SEM, None, None)):
        await _email(
            db,
            cena,
            "ignorado",
            lado,
            integ=integ,
            ficha=ficha,
            pasta=guardar,
            motivo="pasta_sem_categoria",
        )

    async def envio(conversa, mensagem, status="uncertain") -> str:
        job = MailOutbox(
            id=uuid4(),
            mailbox_id=cena.geral.id,
            message_id=getattr(mensagem, "uuid", None),
            author_user_id=dono.id,
            request_id=uuid4(),
            content_enc=encrypt_json(
                {"text": "Oi", "to": "c@d.invalid", "from_address": "21max@tuta.com"}
            ),
            status=status,
        )
        db.add(job)
        await db.flush()
        db.add(MailOutboxMeta(outbox_id=job.id, conversa_id=getattr(conversa, "id", None)))
        await db.flush()
        return str(job.id)

    dele = await _email(db, cena, "gravado", MEU, integ=ml, ficha=loja_ml)
    do_outro = await _email(db, cena, "gravado", OUTRO, integ=oli, ficha=loja_oli)
    envios = {
        MEU: {
            await envio(conversa_minha, dele),
            await envio(conversa_ficha, None),
        },
        SEM: {await envio(conversa_do_site, None)},
        OUTRO: {
            await envio(conversa_dos_outros, None),
            await envio(None, do_outro),
        },
    }
    await db.commit()

    marrocos = await make_user(permissions=dict(PERMISSAO))
    await dar_lojas(db, marrocos, ml, escopo.chave(escopo.FICHA, jlas2.id))
    auth_as(marrocos)
    return SimpleNamespace(
        marrocos=marrocos,
        filas=filas,
        envios=envios,
        avisos=avisos,
        so_dos_outros=so_dos_outros,
        guardar=guardar,
        ml=ml,
        oli=oli,
        jlas2=jlas2,
        outra_ficha=outra_ficha,
        conversa_minha=conversa_minha,
        conversa_ficha=conversa_ficha,
        conversa_dos_outros=conversa_dos_outros,
        conversa_do_site=conversa_do_site,
        dele=dele,
    )


def _visiveis(itens) -> set[str]:
    return {e.id for e in itens if e.lado in (MEU, SEM)}


def _todos(itens) -> set[str]:
    return {e.id for e in itens}


async def _lista(client, fila: str, **params) -> set[str]:
    r = await client.get(f"{EMAIL}/emails", params={"fila": fila, **params})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["itens"]}


# ── As filas: as contagens e as listas ───────────────────────────────────────


async def test_filas_e_listas_mostram_as_lojas_dele_e_o_sem_loja(mundo, client):
    contagens = (await client.get(f"{EMAIL}/filas")).json()
    for fila, itens in mundo.filas.items():
        assert await _lista(client, fila) == _visiveis(itens), fila
        assert contagens[fila] == len(_visiveis(itens)), fila


async def test_o_admin_ve_toda_loja_como_antes(mundo, client, auth_as, cena):
    auth_as(cena.dono)
    contagens = (await client.get(f"{EMAIL}/filas")).json()
    for fila, itens in mundo.filas.items():
        assert await _lista(client, fila) == _todos(itens), fila
        assert contagens[fila] == len(itens), fila
    item = (await client.get(f"{EMAIL}/emails", params={"fila": "sem_loja"})).json()["itens"][0]
    assert item["sugestoes"] and item["pedidos_citados"] and item["abrir_no_tuta"]


async def test_escolher_a_loja_dos_outros_na_lista_nao_abre_nada(mundo, client):
    fila = "informativo"
    assert await _lista(client, fila, integration_id=str(mundo.oli.id)) == set()
    assert await _lista(client, fila, store_info_id=str(mundo.outra_ficha.id)) == set()
    # A pasta com e-mail de todos: só os que ele vê.
    assert await _lista(client, fila, pasta=str(mundo.avisos.id)) == _visiveis(mundo.filas[fila])
    assert await _lista(client, fila, pasta=str(mundo.so_dos_outros.id)) == set()


@pytest.mark.parametrize("lojas", ["so_a_integracao", "so_a_ficha", "nenhuma"])
async def test_o_recorte_vale_em_toda_forma_de_escopo(mundo, client, db, auth_as, make_user, lojas):
    """`integ IN (...)` dá NULL no e-mail só com a ficha: a condição não pode mostrá-lo
    a quem tem só a integração (nem o da integração a quem tem só a ficha)."""
    pessoa = await make_user(permissions=dict(PERMISSAO))
    if lojas == "so_a_integracao":
        await dar_lojas(db, pessoa, mundo.ml)
    elif lojas == "so_a_ficha":
        await dar_lojas(db, pessoa, escopo.chave(escopo.FICHA, mundo.jlas2.id))
    auth_as(pessoa)
    resumos = dict(zip(("jlas2", "outra"), mundo.filas["resumo"], strict=True))
    erros = dict(zip(("sem", "ml", "oli"), mundo.filas["erro"], strict=True))
    assert await _lista(client, "resumo") == (
        {resumos["jlas2"].id} if lojas == "so_a_ficha" else set()
    )
    esperado_erros = {erros["sem"].id} | ({erros["ml"].id} if lojas == "so_a_integracao" else set())
    assert await _lista(client, "erro") == esperado_erros
    r = await client.get(f"{EMAIL}/emails/{resumos['outra'].id}")
    assert r.status_code == 404, "a ficha da outra pessoa"
    r = await client.get(f"{EMAIL}/emails/{resumos['jlas2'].id}")
    assert r.status_code == (200 if lojas == "so_a_ficha" else 404)


# ── O detalhe e o anexo ──────────────────────────────────────────────────────


async def test_detalhe_e_anexo_seguem_a_mesma_regua(mundo, client):
    for fila, itens in mundo.filas.items():
        for e in itens:
            codigo = 200 if e.lado in (MEU, SEM) else 404
            r = await client.get(f"{EMAIL}/emails/{e.id}")
            assert r.status_code == codigo, (fila, e.lado, r.text)
            if fila == "erro":
                continue  # o e-mail com erro não tem texto nem anexo (também para o admin)
            r = await client.get(f"{EMAIL}/anexos/{e.anexo}")
            assert r.status_code == codigo, (fila, e.lado, r.text)
            if codigo == 200:
                assert r.content == b"anexo"
                assert r.headers["content-type"] == "application/octet-stream"


async def test_o_cartao_nao_traz_o_que_so_serve_para_mexer(mundo, client, auth_as, cena):
    sem_loja = mundo.filas["sem_loja"][0]
    for caminho, pegar in (
        (f"{EMAIL}/emails/{sem_loja.id}", lambda r: r),
        (
            f"{EMAIL}/emails?fila=sem_loja",
            lambda r: next(i for i in r["itens"] if i["id"] == sem_loja.id),
        ),
    ):
        cartao = pegar((await client.get(caminho)).json())
        # As sugestões trazem o nome e o id das lojas dos outros.
        assert cartao["sugestoes"] == []
        assert cartao["pedidos_citados"] == []
        assert cartao["abrir_no_tuta"] is None
    r = await client.get(f"{EMAIL}/emails/{sem_loja.id}")
    assert "Oliveira" not in r.text
    # A conversa dele abre; a do site (sem loja, não é dele) não vira link morto.
    dele, _o, _o2, do_site = mundo.filas["suspeito"]
    assert (await client.get(f"{EMAIL}/emails/{do_site.id}")).json()["conversa_id"] is None
    com_conversa = mundo.filas["sem_vinculo"][1]
    assert (await client.get(f"{EMAIL}/emails/{com_conversa.id}")).json()["conversa_id"] == str(
        mundo.conversa_ficha.id
    )
    auth_as(cena.dono)
    cartao = (await client.get(f"{EMAIL}/emails/{do_site.id}")).json()
    assert cartao["conversa_id"] == str(mundo.conversa_do_site.id)
    assert cartao["sugestoes"] and cartao["abrir_no_tuta"]


# ── As pastas informativas ───────────────────────────────────────────────────


async def test_informativos_contam_so_o_que_ele_ve(mundo, client, auth_as, cena):
    itens = (await client.get(f"{EMAIL}/informativos")).json()["itens"]
    assert [i["pasta_id"] for i in itens] == [str(mundo.avisos.id)], "a pasta só dos outros some"
    avisos = itens[0]
    assert avisos["total"] == len(_visiveis(mundo.filas["informativo"]))
    assert "Oliveira" not in avisos["lojas"] and "Outra Temu" not in avisos["lojas"]
    assert "JLAS2" in avisos["lojas"]
    auth_as(cena.dono)
    itens = (await client.get(f"{EMAIL}/informativos")).json()["itens"]
    por_pasta = {i["pasta_id"]: i for i in itens}
    assert por_pasta[str(mundo.avisos.id)]["total"] == 5
    assert por_pasta[str(mundo.so_dos_outros.id)]["total"] == 1


# ── Revisar envio ────────────────────────────────────────────────────────────


async def test_revisar_envio_pela_loja_da_conversa_ou_do_email(mundo, client, auth_as, cena):
    r = await client.get(f"{EMAIL}/envios", params={"estado": "revisar"})
    assert r.status_code == 200, r.text
    itens = {i["id"]: i for i in r.json()["itens"]}
    assert set(itens) == mundo.envios[MEU] | mundo.envios[SEM]
    assert (await client.get(f"{EMAIL}/filas")).json()["revisar_envio"] == len(itens)
    # A do site aparece, sem o link da conversa (ele não abre).
    assert itens[next(iter(mundo.envios[SEM]))]["conversa_id"] is None
    assert {i["conversa_id"] for k, i in itens.items() if k in mundo.envios[MEU]} == {
        str(mundo.conversa_minha.id),
        str(mundo.conversa_ficha.id),
    }
    auth_as(cena.dono)
    r = await client.get(f"{EMAIL}/envios", params={"estado": "revisar"})
    todos = mundo.envios[MEU] | mundo.envios[SEM] | mundo.envios[OUTRO]
    assert {i["id"] for i in r.json()["itens"]} == todos
    assert (await client.get(f"{EMAIL}/filas")).json()["revisar_envio"] == len(todos)


# ── Pastas, regras e saúde ───────────────────────────────────────────────────


async def test_pastas_e_regras_com_os_numeros_dele(mundo, client, db, auth_as, cena):
    r = await client.get(f"{EMAIL}/pastas")
    assert r.status_code == 200, r.text
    por_id = {p["id"]: p for p in r.json()["itens"]}
    guardar, avisos = por_id[str(mundo.guardar.id)], por_id[str(mundo.avisos.id)]
    assert (guardar["total_30d"], guardar["guardados"]) == (2, 2), "o dele e o sem loja"
    assert avisos["total_30d"] == len(_visiveis(mundo.filas["informativo"]))
    # A pasta sem nenhum e-mail dele não aparece, nem o nome.
    assert str(mundo.so_dos_outros.id) not in por_id
    assert (await client.get(f"{EMAIL}/regras")).status_code == 200
    auth_as(cena.dono)
    por_id = {p["id"]: p for p in (await client.get(f"{EMAIL}/pastas")).json()["itens"]}
    guardar = por_id[str(mundo.guardar.id)]
    assert (guardar["total_30d"], guardar["guardados"]) == (3, 3)


async def test_pastas_da_caixa_bloqueada_para_ele_somem(mundo, client, db):
    db.add(
        MailAcesso(mailbox_id=mundo.avisos.mailbox_id, user_id=mundo.marrocos.id, modo="bloqueado")
    )
    await db.commit()
    r = await client.get(f"{EMAIL}/pastas")
    assert r.status_code == 200 and not r.json()["itens"]
    assert (await client.get(f"{EMAIL}/informativos")).json()["itens"] == []


async def test_saude_so_com_os_numeros_dele(mundo, client, auth_as, cena):
    r = await client.get(f"{EMAIL}/saude")
    assert r.status_code == 200, r.text
    corpo = r.json()
    # A caixa privada continua só para o dono/admin.
    assert [c["nome"] for c in corpo["caixas"]] == [cena.geral.label]
    geral = corpo["caixas"][0]
    # 2 dele + 1 sem loja nos erros; os da Oliveira e da outra ficha não contam.
    assert geral["emails"]["erro"] == 2
    assert geral["emails"]["resumo"] == 1
    assert geral["emails"]["informativo"] == len(_visiveis(mundo.filas["informativo"]))
    assert geral["envios_a_conferir"] == 3
    # As contagens de pasta olham o e-mail de toda loja: não vêm.
    assert geral["pastas_sem_revisar"] is None and geral["pastas_sem_categoria"] is None
    # Os avisos de cadastro: só das lojas dele.
    dele = {str(mundo.jlas2.id), str(cena.loja_ml.id)}
    for chave in ("lojas_sem_caixa_lida", "cadastro_incompleto", "lojas_sem_integracao"):
        assert {lj["store_info_id"] for lj in corpo[chave]} <= dele, chave
    assert str(mundo.jlas2.id) in {lj["store_info_id"] for lj in corpo["lojas_sem_integracao"]}
    auth_as(cena.dono)
    corpo = (await client.get(f"{EMAIL}/saude")).json()
    geral = next(c for c in corpo["caixas"] if c["nome"] == cena.geral.label)
    assert geral["emails"]["erro"] == 3 and geral["envios_a_conferir"] == 5
    assert geral["pastas_sem_revisar"] is not None
    assert str(mundo.outra_ficha.id) in {
        lj["store_info_id"] for lj in corpo["lojas_sem_integracao"]
    }


# ── As conversas, a busca e as contagens (as rotas que já tinham o escopo) ───


async def test_conversas_busca_e_contagens_so_as_dele(db, cena, client, make_user, auth_as):
    h = await _historico(db, cena)
    marrocos = await make_user(permissions=dict(PERMISSAO))
    await dar_lojas(db, marrocos, cena.ml, escopo.chave(escopo.FICHA, cena.loja_ml.id))
    auth_as(marrocos)

    async def ids(**params):
        r = await client.get(
            f"{URL}/conversas", params={"caixa": "email", "filtro": "todas", **params}
        )
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["itens"]}

    assert await ids() == {h["ml"]}
    assert await ids(q="Joana") == {h["ml"]}
    assert await ids(q="Pedro") == set(), "a busca não acha a conversa da Oliveira"
    assert await ids(q="US-26-0002") == set(), "nem o chamado do site"
    assert await ids(integration_id=str(cena.oliveira_ml.id)) == set()

    r = await client.get(f"{URL}/conversas/{h['oliveira']}")
    assert r.status_code == 404
    assert (await client.get(f"{URL}/conversas/{h['ml']}")).status_code == 200

    contagens = (await client.get(f"{EMAIL}/contagens")).json()
    assert contagens["chamados"] == 0
    por_loja = (await client.get(f"{EMAIL}/contagens", params={"por_loja": "true"})).json()
    assert str(cena.ml.id) in {i["integration_id"] for i in por_loja["por_loja"]}
    assert str(cena.oliveira_ml.id) not in {i["integration_id"] for i in por_loja["por_loja"]}
    assert por_loja["sites_na_barra"] == []

    # A caixa de cada loja: a dos outros não traz nada.
    r = await client.get(
        f"{EMAIL}/lojas/emails",
        params={"grupo": "problemas", "integration_id": str(cena.oliveira_ml.id)},
    )
    assert r.status_code == 200 and r.json()["itens"] == []


# ── As ações: só de admin ────────────────────────────────────────────────────


def _acoes_do_email() -> list[tuple[str, str]]:
    return sorted(
        (metodo, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith(f"{EMAIL}/")
        for metodo in r.methods
        if metodo not in ("GET", "HEAD", "OPTIONS") and "/admin/" not in r.path
    )


async def test_toda_acao_da_aba_e_403_e_nao_muda_nada(mundo, client, db, cena):
    acoes = _acoes_do_email()
    assert {
        ("POST", f"{EMAIL}/emails/{{message_id}}/loja"),
        ("POST", f"{EMAIL}/emails/{{message_id}}/vincular"),
        ("POST", f"{EMAIL}/emails/{{message_id}}/ignorar"),
        ("POST", f"{EMAIL}/emails/{{message_id}}/reprocessar"),
        ("POST", f"{EMAIL}/reprocessar"),
        ("POST", f"{EMAIL}/envios/{{job_id}}/resolver"),
        ("POST", f"{EMAIL}/conversas/{{conversa_id}}/vincular"),
        ("POST", f"{EMAIL}/conversas/{{conversa_id}}/agrupar"),
        ("POST", f"{EMAIL}/conversas/{{conversa_id}}/nao-agrupar"),
        ("PATCH", f"{EMAIL}/pastas/{{pasta_id}}"),
        ("PUT", f"{EMAIL}/regras"),
    } <= set(acoes)
    dele = mundo.filas["sem_loja"][1]
    job = next(iter(mundo.envios[MEU]))
    corpos = {
        "/loja": {"store_info_id": str(cena.loja_ml.id)},
        "/vincular": {"pedido": "123"},
        "/resolver": {"saiu": True},
        "/agrupar": {"conversa_id": str(mundo.conversa_ficha.id)},
    }
    for metodo, molde in acoes:
        caminho = (
            molde.replace("{message_id}", dele.id)
            .replace("{job_id}", job)
            .replace("{conversa_id}", str(mundo.conversa_minha.id))
            .replace("{pasta_id}", str(mundo.avisos.id))
        )
        corpo = next((c for fim, c in corpos.items() if molde.endswith(fim)), None)
        if molde.endswith("/regras"):
            corpo = {"tipo": "plataforma", "palavra": "kwai", "valor": "ml"}
        if "/pastas/" in molde:
            corpo = {"categoria": None, "ignorar": True}
        r = await client.request(metodo, caminho, json=corpo)
        assert r.status_code == 403, (metodo, molde, r.text)
        assert r.json()["detail"]["code"] == "atendimento_so_leitura", (metodo, molde)
    pasta_id = mundo.avisos.id
    db.expire_all()
    meta = await db.get(MailMessageMeta, dele.uuid)
    assert meta.estado == "sem_loja" and meta.integration_id is None
    assert (await db.get(MailFolder, pasta_id)).categoria == "informativo"
    assert (await db.get(MailOutboxMeta, UUID(job))).resolucao is None


# ── Quem não tem a permissão não muda ────────────────────────────────────────


@pytest.mark.parametrize(
    ("perms", "saude_abre"),
    [
        (None, False),
        ({"atendimento": {"view": True, "edit": True, "delete": True}}, False),
        # A antiga (londres, israel) não abre nada.
        ({"atendimento_email": {"view": True, "edit": True, "delete": True}}, False),
        ({"atendimento_email_aba": {"view": False, "edit": True}}, False),
        # A leitura da caixa abre a saúde, como ontem, e não as filas.
        ({"atendimento_email_leitura": {"view": True}}, True),
    ],
)
async def test_quem_nao_tem_a_permissao_nao_muda(
    mundo, client, db, make_user, auth_as, perms, saude_abre
):
    pessoa = await make_user(permissions=perms)
    await dar_lojas(db, pessoa, mundo.ml)
    auth_as(pessoa)
    assert not acesso.so_le_filas_email(pessoa)
    dele = mundo.filas["sem_vinculo"][0]
    for caminho in (
        "filas",
        "emails",
        f"emails/{dele.id}",
        "informativos",
        "envios",
        "pastas",
        "regras",
    ):
        r = await client.get(f"{EMAIL}/{caminho}")
        assert r.status_code == 403, (caminho, r.text)
        assert r.json()["detail"]["code"] == "atendimento_so_quem_mexe", caminho
    r = await client.get(f"{EMAIL}/saude")
    if saude_abre:
        geral = r.json()["caixas"]
        assert r.status_code == 200 and "lojas_sem_integracao" not in r.json()
        assert all(c["emails"].get("erro", 0) in (0, 3) for c in geral), "sem o recorte"
    else:
        assert r.status_code == 403 and r.json()["detail"]["code"] == "aba_email_so_admin"
    # O anexo do e-mail sem conversa continua sendo de quem mexe.
    sem = mundo.filas["sem_loja"][0]
    assert (await client.get(f"{EMAIL}/anexos/{sem.anexo}")).status_code == 404


async def test_admin_fora_da_lista_nao_ganha_as_filas(
    mundo, client, db, make_user, auth_as, monkeypatch
):
    """O admin fora de ATENDIMENTO_USUARIOS não mexe: as filas continuam 403, mesmo
    com a permissão (o admin não entra no recorte, que seria de loja nenhuma)."""
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "outra@pessoa.invalid")
    admin = await make_user(role=UserRole.ADMIN, permissions=dict(PERMISSAO))
    auth_as(admin)
    assert not acesso.le_filas_email(admin)
    r = await client.get(f"{EMAIL}/filas")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "atendimento_so_quem_mexe"
    assert (await client.get(f"{EMAIL}/saude")).status_code == 200


async def test_sem_loja_nenhuma_ve_so_o_sem_loja(mundo, client, db, make_user, auth_as):
    pessoa = await make_user(permissions=dict(PERMISSAO))
    auth_as(pessoa)
    assert await _lista(client, "sem_loja") == {
        e.id for e in mundo.filas["sem_loja"] if e.lado == SEM
    }
    assert await _lista(client, "resumo") == set()


# ── A Central continua fechada ───────────────────────────────────────────────


async def test_a_central_continua_fechada_para_ele(mundo, client, cena):
    for caminho in (
        "/api/mail/mailboxes",
        f"/api/mail/mailboxes/{cena.geral.id}/messages",
        f"/api/mail/mailboxes/{cena.geral.id}/pastas",
        f"/api/mail/mailboxes/{cena.geral.id}/lista",
        f"/api/mail/messages/{mundo.dele.id}",
    ):
        r = await client.get(caminho)
        assert r.status_code == 403, (caminho, r.text)
        assert r.json()["detail"]["code"] == "admin_only", caminho
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "user" and me["atendimento_mexe"] is False


async def test_sem_loja_com_so_lojas_dos_outros_nao_aparece(mundo, db, cena, client):
    """04/11/2026 ("as lojas seguem só as dele"): o e-mail sem loja com sugestão só de loja
    dos outros não aparece, nem o corpo; com uma das dele, ou sem sugestão de loja, aparece."""

    def sugestao(integ, loja) -> dict:
        return {
            "store_info_id": str(loja.id),
            "nome": "Loja",
            "plataforma": "ml",
            "integration_id": str(integ.id),
            "sugestao": True,
        }

    dos_outros = await _email(
        db, cena, "sem_loja", "x", sugestoes=[sugestao(cena.oliveira_ml, cena.loja_oliveira_ml)]
    )
    com_a_dele = await _email(
        db,
        cena,
        "sem_loja",
        "x",
        sugestoes=[
            sugestao(cena.oliveira_ml, cena.loja_oliveira_ml),
            sugestao(cena.ml, cena.loja_ml),
        ],
    )
    sem_sugestao = await _email(db, cena, "sem_loja", "x", sugestoes=[])
    await db.commit()
    ids = await _lista(client, "sem_loja")
    assert dos_outros.id not in ids
    assert {com_a_dele.id, sem_sugestao.id} <= ids
    assert (await client.get(f"{EMAIL}/emails/{dos_outros.id}")).status_code == 404
    assert (await client.get(f"{EMAIL}/emails/{sem_sugestao.id}")).status_code == 200
