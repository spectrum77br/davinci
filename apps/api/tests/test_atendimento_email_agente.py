"""O AGENTE do atendimento respondendo E-MAIL (12/11/2026, `services/atendimento/email_agente.py`).

- a CHAVE é a do atendimento: a loja pelo canal do pós-compra; o SAC do site pela
  linha do site (`site:<site>`), ligada na aba E-mail › Agente (só admin);
- a TRAVA do e-mail: só a caixa SAC do site ou a loja com integração; nunca o
  aviso, o suspeito, o histórico de vendas; sem a chave, a IA continua sem
  responder e-mail (`test_mail_responder.test_a_ia_nunca_responde_email`);
- o PACOTE ganha o bloco E-MAIL (assinatura do sistema, o formulário de SAC do
  site com o link e o que pede, as regras do e-mail);
- o envio do agente sai pelo MESMO caminho da pessoa (a fila da Central, a
  assinatura da marca), com o usuário do agente, só no automático;
- o pedido do formulário passa por cima só das travas do ASSUNTO;
- a tela (GET/PUT/POST/PATCH/DELETE /api/atendimento/email/agente) é só de admin.

Postgres local + HTTP em processo; nada de modelo, Tuta ou envio de verdade.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, MailOutbox, User, UserRole
from app.models.mail_agente import MailAgenteRegra
from app.security.cipher import decrypt_json
from app.services.atendimento import email_agente, enviar, ia, validador
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    MOTIVO_EMAIL_SO_PESSOA,
    ORIGEM_IA,
    motivo_canal_sem_envio,
)
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte
from tests.test_mail_ponte import entrar, meta_de, rodar
from tests.test_mail_responder import _jobs, _ligar_envio

API = "/api/atendimento/email/agente"
URL_URANYX = "https://uranyx.com.br/sac"
PEDIDO = (
    "Olá, Maria! Para analisarmos o defeito, abra o atendimento pelo formulário de SAC: "
    f"{URL_URANYX}. Ao enviar, você recebe o protocolo na hora."
)


@pytest.fixture
def agente_ligado(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_ia_ativa", True)
    monkeypatch.setattr(s, "atendimento_agente_ativo", True)
    monkeypatch.setattr(s, "atendimento_auto_ativo", True)
    monkeypatch.setattr(s, "atendimento_agente_api_key", "sk-ant-teste")
    return s


async def _canal_do_site(db, site: str = "uranyx") -> AtendimentoCanal:
    canal = AtendimentoCanal(
        id=uuid4(),
        plataforma="site",
        canal="carrinho",
        externo_ref=f"site:{site}",
        modo="observar",
        status="ok",
    )
    db.add(canal)
    await db.commit()
    return canal


async def _regra(db, **kw) -> MailAgenteRegra:
    r = MailAgenteRegra(
        chave=email_agente.CHAVE_FORMULARIO,
        titulo="Reclamação no SAC sem o formulário",
        instrucao="Peça para abrir pelo formulário de SAC do site.",
        ordem=0,
        responde_sozinho=True,
        **kw,
    )
    db.add(r)
    await db.commit()
    return r


async def _sac_direto(db, cena, **kw) -> AtendimentoConversa:
    """O cliente escreve DIRETO no sac@ (sem o formulário do site)."""
    campos = {
        "pasta": "INBOX",
        "de": "maria@gmail.com",
        "de_nome": "Maria",
        "para": ["sac@uranyx.com.br"],
        "assunto": "Air fryer parou de funcionar",
        "corpo": "Comprei a air fryer e ela não liga mais.",
        **kw,
    }
    mid = await entrar(db, cena.geral, **campos)
    await rodar(db)
    meta = await meta_de(db, mid)
    assert meta.conversa_id is not None, meta.estado
    return await db.get(AtendimentoConversa, meta.conversa_id)


# ─────────────── puras ───────────────


def test_bloco_do_pacote_tem_assinatura_formulario_e_regras():
    chave = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx")
    regra = SimpleNamespace(titulo="Regra X", instrucao="Faça Y.", chave=None, id=uuid4())
    bloco = email_agente.bloco_do_pacote(chave, [regra])
    assert "E-MAIL" in bloco and "NÃO escreva assinatura" in bloco
    assert "NÃO veio pelo formulário" in bloco
    assert URL_URANYX in bloco and "número de série" in bloco
    assert f"- [email:{str(regra.id)[:8]}] Regra X: Faça Y." in bloco
    assert "https://uranyx.com.br" in bloco and "evidencias" in bloco
    andamento = email_agente.Chave(
        canal_id=uuid4(), modo=MODO_AUTO, site="uranyx", em_andamento=True
    )
    assert "atendimento em andamento" in email_agente.bloco_do_pacote(andamento, [])
    charlots = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="charlots")
    assert "https://charlots.com.br/sac" in email_agente.bloco_do_pacote(charlots, [])
    assert "tamanho" in email_agente.bloco_do_pacote(charlots, [])
    veio = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx", via_formulario=True)
    assert "VEIO pelo formulário" in email_agente.bloco_do_pacote(veio, [])
    loja = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO)
    assert "formulário" not in email_agente.bloco_do_pacote(loja, []).lower()


def test_pedido_do_formulario_so_com_o_link_da_marca_e_sem_protocolo():
    chave = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx")
    assert email_agente.pedido_do_formulario(PEDIDO, chave)
    assert email_agente.pedido_do_formulario(f"Abra em {URL_URANYX}/.", chave)
    assert not email_agente.pedido_do_formulario("Sem link nenhum.", chave)
    assert not email_agente.pedido_do_formulario(f"{URL_URANYX} e https://x.com", chave)
    assert email_agente.pedido_do_formulario(f"{URL_URANYX} ou https://uranyx.com.br", chave)
    andamento = email_agente.Chave(
        canal_id=uuid4(), modo=MODO_AUTO, site="uranyx", em_andamento=True
    )
    assert not email_agente.pedido_do_formulario(PEDIDO, andamento)
    assert not email_agente.pedido_do_formulario("https://charlots.com.br/sac", chave)
    veio = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx", via_formulario=True)
    assert not email_agente.pedido_do_formulario(PEDIDO, veio)
    assert not email_agente.pedido_do_formulario(PEDIDO, None)


def test_site_da_marca_e_via_formulario():
    def c(marca, protocolo=None):
        return SimpleNamespace(
            plataforma="site", dados={"mail": {"marca": marca, "protocolo": protocolo}}
        )

    assert email_agente.site_da_conversa(c("uranyx")) == "uranyx"
    assert email_agente.site_da_conversa(c("charlots-park")) == "charlots"
    assert email_agente.site_da_conversa(c("outra")) is None
    assert email_agente.via_formulario(c("uranyx", "US-26-0001"))
    assert not email_agente.via_formulario(c("uranyx"))


def test_email_em_massa_nem_vai_ao_agente():
    assert email_agente.parece_em_massa("Promoção! Abrir no navegador")
    assert email_agente.parece_em_massa("Não consegue visualizar? Veja online")
    assert email_agente.parece_em_massa("/// Painel · Atacado Novo cadastro para analisar")
    assert email_agente.parece_em_massa("Oferta" + "\u034f " * 10)
    assert not email_agente.parece_em_massa("Comprei a air fryer e ela não liga mais.")
    assert not email_agente.parece_em_massa(None)


def test_libera_pela_regra_so_a_citada_e_marcada_no_automatico():
    auto = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx")
    que_saem = frozenset({"email:formulario_sac", "email:abcd1234"})
    assert email_agente.libera_pela_regra(["email:abcd1234"], "Olá!", auto, que_saem)
    assert not email_agente.libera_pela_regra(["email:outra"], "Olá!", auto, que_saem)
    assert not email_agente.libera_pela_regra([], "Olá!", auto, que_saem)
    # A do formulário só com o pedido do formulário de verdade.
    assert email_agente.libera_pela_regra(["email:formulario_sac"], PEDIDO, auto, que_saem)
    assert not email_agente.libera_pela_regra(["email:formulario_sac"], "Sem link.", auto, que_saem)
    copiloto = email_agente.Chave(canal_id=uuid4(), modo=MODO_COPILOTO, site="uranyx")
    assert not email_agente.libera_pela_regra(["email:abcd1234"], "Olá!", copiloto, que_saem)


def test_validador_libera_so_o_link_do_formulario_no_email_do_agente():
    def erros(texto, links):
        return validador.validar(
            texto, plataforma="site", canal="email", origem=ORIGEM_IA, links_permitidos=links
        )

    assert erros(PEDIDO, [URL_URANYX]) == []
    assert erros(PEDIDO, []) != []
    assert erros("Veja https://outro.com.br/sac", [URL_URANYX]) != []
    # Fora do e-mail (e da Shopee) o link continua barrado.
    assert (
        validador.validar(
            PEDIDO,
            plataforma="ml",
            canal="pos_venda",
            origem=ORIGEM_IA,
            links_permitidos=[URL_URANYX],
        )
        != []
    )


def test_liberar_vale_so_no_bloco():
    conversa = SimpleNamespace(id=uuid4(), canal_id=None)
    chave = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx")
    assert not email_agente.liberada(conversa) and email_agente.canal_id(conversa) is None
    with email_agente.liberar(conversa.id, chave):
        assert email_agente.liberada(conversa)
        assert email_agente.canal_id(conversa) == chave.canal_id
        assert email_agente.links_permitidos(conversa) == [URL_URANYX, "https://uranyx.com.br"]
    assert not email_agente.liberada(conversa)


# ─────────────── a chave e a trava (banco) ───────────────


async def test_sac_do_site_so_com_a_chave_do_site_ligada(db, cena, agente_ligado):
    await _canal_do_site(db)
    c = await _sac_direto(db, cena)
    assert motivo_canal_sem_envio(c.canal, c.plataforma, c.dados) == MOTIVO_EMAIL_SO_PESSOA
    assert await email_agente.motivo_fora(db, c) is None
    assert await email_agente.chave_liberada(db, c) is None  # o site em observar
    await email_agente.ligar_site(db, "uranyx", modo=MODO_COPILOTO)
    await db.commit()
    chave = await email_agente.chave_liberada(db, c)
    assert chave is not None and chave.modo == MODO_COPILOTO
    assert (chave.site, chave.via_formulario) == ("uranyx", False)
    # Sem a chave Claude no servidor, nada.
    agente_ligado.atendimento_agente_api_key = ""
    agente_ligado.atendimento_llm_api_key = ""
    assert await email_agente.chave_liberada(db, c) is None


async def test_trava_do_email_caixa_aviso_e_loja(db, cena, agente_ligado):
    await _canal_do_site(db)
    await email_agente.ligar_site(db, "uranyx", modo=MODO_AUTO)
    await db.commit()
    # A caixa Admin do site (atacado@): fica com a pessoa.
    admin = await _sac_direto(db, cena, para=["atacado@uranyx.com.br"], assunto="Atacado")
    assert await email_agente.motivo_fora(db, admin) == email_agente.FORA_CAIXA
    assert await email_agente.chave_liberada(db, admin) is None
    # O aviso de plataforma no SAC não é cliente.
    aviso = await _sac_direto(
        db, cena, de="noreply@reclameaqui.com.br", de_nome="Reclame Aqui", assunto="Nova reclamação"
    )
    assert await email_agente.motivo_fora(db, aviso) in (
        email_agente.FORA_NAO_E_CLIENTE,
        email_agente.FORA_INFORMATIVO,
    )


async def test_em_massa_fica_fora_e_resposta_da_equipe_marca_em_andamento(db, cena, agente_ligado):
    await _canal_do_site(db)
    await email_agente.ligar_site(db, "uranyx", modo=MODO_AUTO)
    await db.commit()
    massa = await _sac_direto(
        db, cena, de="news@loja.com.br", assunto="Black", corpo="Oferta! Abrir no navegador"
    )
    assert await email_agente.motivo_fora(db, massa) == email_agente.FORA_EM_MASSA
    c = await _sac_direto(db, cena)
    chave = await email_agente.chave_liberada(db, c)
    assert chave is not None and chave.em_andamento is False


async def test_loja_fica_de_fora_por_enquanto_e_usa_o_pos_compra_quando_ligar(
    db, cena, agente_ligado, monkeypatch
):
    c = SimpleNamespace(
        id=uuid4(),
        canal="email",
        plataforma="ml",
        integration_id=cena.ml.id,
        canal_id=None,
        dados={"fonte": "tuta", "mail": {}},
    )
    assert await email_agente.canal_da_chave(db, c) is None
    pos = AtendimentoCanal(
        id=uuid4(), integration_id=cena.ml.id, plataforma="ml", canal="pos_venda", modo="auto"
    )
    db.add(pos)
    db.add(
        AtendimentoCanal(
            id=uuid4(), integration_id=cena.ml.id, plataforma="ml", canal="pergunta", modo="auto"
        )
    )
    await db.commit()
    assert await email_agente.canal_da_chave(db, c) is None  # o dono: só os sites
    monkeypatch.setattr(email_agente, "LOJAS_LIGADAS", True)
    assert (await email_agente.canal_da_chave(db, c)).id == pos.id


# ─────────────── o envio pelo caminho da pessoa ───────────────


async def test_agente_envia_pela_fila_com_a_assinatura_so_no_automatico(
    db, cena, agente_ligado, monkeypatch
):
    async def _sem_motivos(*_a, **_k):
        return []

    monkeypatch.setattr(ia, "_motivos_humano_para_auto", _sem_motivos)
    await _canal_do_site(db)
    c = await _sac_direto(db, cena)
    await _ligar_envio(db, cena.geral)
    # Sem a chave: a IA continua sem responder e-mail.
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_resposta(db, c, PEDIDO, user=None, origem=ORIGEM_IA)
    assert e.value.code == "auto_desligado"
    # Copiloto: só sugere.
    await email_agente.ligar_site(db, "uranyx", modo=MODO_COPILOTO)
    await db.commit()
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_resposta(db, c, PEDIDO, user=None, origem=ORIGEM_IA)
    assert e.value.code == "auto_desligado"
    # Automático: sai pela fila da Central, do sac@, com a assinatura da marca.
    await email_agente.ligar_site(db, "uranyx", modo=MODO_AUTO)
    await db.commit()
    c = await db.get(AtendimentoConversa, c.id, populate_existing=True)
    m = await enviar.enviar_resposta(db, c, PEDIDO, user=None, origem=ORIGEM_IA)
    assert m.status == "enviando" and m.origem == ORIGEM_IA
    [job] = await _jobs(db)
    carga = decrypt_json(job.content_enc)
    assert carga["from_address"] == "sac@uranyx.com.br"
    assert carga["to"] == "maria@gmail.com"
    assert PEDIDO in carga["text"] and "Equipe Uranyx" in carga["text"]
    autor = await db.get(User, job.author_user_id)
    assert autor.open_id == email_agente.OPEN_ID_AGENTE


async def test_agente_nao_envia_link_que_nao_e_o_do_formulario(
    db, cena, agente_ligado, monkeypatch
):
    async def _sem_motivos(*_a, **_k):
        return []

    monkeypatch.setattr(ia, "_motivos_humano_para_auto", _sem_motivos)
    await _canal_do_site(db)
    c = await _sac_direto(db, cena)
    await _ligar_envio(db, cena.geral)
    await email_agente.ligar_site(db, "uranyx", modo=MODO_AUTO)
    await db.commit()
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_resposta(
            db, c, "Abra em https://golpe.com/sac por favor.", user=None, origem=ORIGEM_IA
        )
    assert e.value.code == "texto_invalido"
    assert (await db.execute(select(MailOutbox))).first() is None


async def test_regra_que_responde_sozinho_passa_so_pelas_travas_do_assunto(db, cena, agente_ligado):
    await _regra(db)
    charlots = MailAgenteRegra(
        titulo="Só Charlots",
        instrucao="Algo da Charlots.",
        sites=["charlots"],
        responde_sozinho=True,
    )
    db.add(charlots)
    await db.commit()

    def rasc(texto, *evid):
        return SimpleNamespace(texto=texto, fatos={"agente": {"evidencias": list(evid)}})

    conversa = SimpleNamespace(id=uuid4(), canal_id=None)
    auto = email_agente.Chave(canal_id=uuid4(), modo=MODO_AUTO, site="uranyx")
    with email_agente.liberar(conversa.id, auto):
        assert await email_agente.sai_pela_regra(db, conversa, rasc(PEDIDO, "email:formulario_sac"))
        assert not await email_agente.sai_pela_regra(db, conversa, rasc(PEDIDO))
        assert not await email_agente.sai_pela_regra(
            db, conversa, rasc("Sem link.", "email:formulario_sac")
        )
        # A regra só da Charlots não vale na Uranyx.
        so_charlots = email_agente.id_da_regra(charlots)
        assert not await email_agente.sai_pela_regra(db, conversa, rasc("Olá!", so_charlots))
    copiloto = email_agente.Chave(canal_id=uuid4(), modo=MODO_COPILOTO, site="uranyx")
    with email_agente.liberar(conversa.id, copiloto):
        assert not await email_agente.sai_pela_regra(
            db, conversa, rasc(PEDIDO, "email:formulario_sac")
        )
    r = await db.scalar(select(MailAgenteRegra).where(MailAgenteRegra.chave.is_not(None)))
    r.ativa = False
    await db.commit()
    with email_agente.liberar(conversa.id, auto):
        assert not await email_agente.sai_pela_regra(
            db, conversa, rasc(PEDIDO, "email:formulario_sac")
        )
    assert "acao_pessoa" not in email_agente.TRAVAS_DO_ASSUNTO
    assert "sem_auto" not in email_agente.TRAVAS_DO_ASSUNTO


async def test_rodada_so_pega_email_com_a_chave(db, cena, agente_ligado, monkeypatch):
    chamadas: list = []

    async def _gerar(session, conversa, **_k):
        chamadas.append((conversa.id, email_agente.liberada(conversa)))
        return None

    monkeypatch.setattr(ia, "gerar_rascunho", _gerar)
    monkeypatch.setattr(ia, "ESPERA_SILENCIO", ia.timedelta(0))
    await _canal_do_site(db)
    c = await _sac_direto(db, cena)
    await email_agente.gerar_pendentes(db)
    assert chamadas == []
    await email_agente.ligar_site(db, "uranyx", modo=MODO_AUTO)
    await db.commit()
    await email_agente.gerar_pendentes(db)
    assert chamadas == [(c.id, True)]


# ─────────────── a tela (só admin) ───────────────


async def test_tela_so_admin_liga_site_e_mexe_nas_regras(
    db, cena, client, auth_as, make_user, agente_ligado
):
    await _canal_do_site(db)
    fixa = await _regra(db)
    equipe = await make_user(role=UserRole.USER)
    auth_as(equipe)
    assert (await client.get(API)).status_code == 403
    auth_as(cena.dono)
    r = await client.get(API)
    assert r.status_code == 200, r.text
    corpo = r.json()
    uranyx = next(s for s in corpo["sites"] if s["site"] == "uranyx")
    assert (uranyx["ligado"], uranyx["formulario"]) == (False, URL_URANYX)
    assert corpo["regras"][0]["fixa"] is True
    r = await client.put(f"{API}/sites/uranyx", json={"modo": "auto"})
    assert r.status_code == 200, r.text
    uranyx = next(s for s in r.json()["sites"] if s["site"] == "uranyx")
    assert (uranyx["ligado"], uranyx["modo"]) == (True, "auto")
    r = await client.put(f"{API}/sites/charlots", json={"modo": "auto"})
    assert r.status_code == 409  # sem a linha do site
    r = await client.put(f"{API}/sites/uranyx", json={"modo": None})
    assert next(s for s in r.json()["sites"] if s["site"] == "uranyx")["ligado"] is False
    r = await client.post(
        f"{API}/regras",
        json={
            "titulo": "Tom",
            "instrucao": "Sempre trate por você.",
            "sites": ["charlots"],
            "responde_sozinho": True,
        },
    )
    assert r.status_code == 201, r.text
    assert (r.json()["sites"], r.json()["responde_sozinho"]) == (["charlots"], True)
    assert r.json()["id_agente"].startswith("email:")
    assert corpo["lojas"] == []  # só os sites por enquanto
    nova = r.json()["id"]
    r = await client.patch(f"{API}/regras/{nova}", json={"ativa": False, "sites": []})
    assert (r.json()["ativa"], r.json()["sites"]) == (False, [])
    r = await client.post(
        f"{API}/regras", json={"titulo": "X", "instrucao": "y" * 10, "sites": ["x"]}
    )
    assert r.status_code == 422
    assert (await client.delete(f"{API}/regras/{fixa.id}")).status_code == 409
    assert (await client.delete(f"{API}/regras/{nova}")).status_code == 204
