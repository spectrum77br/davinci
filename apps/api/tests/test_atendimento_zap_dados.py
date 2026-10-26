# ruff: noqa: S105, S106  (segredos de teste, nada real)
"""Zap (WhatsApp Cloud API, RF10) — a camada de DADOS da Onda 1 (docs/zap/desenho.md).

O que se garante aqui (o webhook, o processador e a tela constroem em cima):

- o vocabulário (`constantes`): `whatsapp` é plataforma EXTERNA (na lista,
  fora da caixa do sync/IA/manual), com a caixa `zap`, `observar` ou (desde
  a Onda 2, 26/10/2026) `humano`, prazo de 24 h e 4.096 caracteres; o Zap
  do número responde só por pessoa (`MOTIVO_ZAP_SO_PESSOA`: a IA e a
  automática ficam de fora) e o formato antigo (Zap com a plataforma da
  venda) continua recusado (P2);
- a referência do canal (`canais_externos`): `zap:<phone_number_id>`, o
  `garantir_canal` do número (nasce em `observar`), o nome na barra e o
  filtro `?externo_ref=zap:` da lista (sem 422);
- as seis tabelas (`models/atendimento_zap.py`, migration 0430): tudo nasce
  desligado, os segredos ficam cifrados em repouso, e o banco segura o que
  importa — um contato por BSUID e por telefone normalizado em cada número,
  um evento por `chave`, uma mídia por `media_id` em cada número e os CHECKs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoCanal,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.security import cipher
from app.services.atendimento import canais_externos, gravar
from app.services.atendimento.constantes import (
    CANAIS_EXTERNOS,
    CANAL_ZAP,
    LIMITE_CARACTERES,
    MODO_HUMANO,
    MODO_OBSERVAR,
    MODOS_EXTERNOS,
    MOTIVO_ZAP_SEM_ENVIO,
    MOTIVO_ZAP_SO_PESSOA,
    PLATAFORMA_WHATSAPP,
    PLATAFORMAS,
    PLATAFORMAS_CAIXA,
    PLATAFORMAS_EXTERNAS,
    PLATAFORMAS_LISTA,
    SLA_HORAS,
    motivo_canal_sem_envio,
    rotulo_tipo_plataforma,
)

URL = "/api/atendimento"
PNID = "106540352242922"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """A caixa é só admin (rota.SO_ADMIN); os testes usam o recurso fino, como os outros."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    # Só administrador vê o Zap (decisão do Eduardo, 26/10/2026).
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


# ── Vocabulário (puro) ────────────────────────────────────────────────────


def test_whatsapp_e_plataforma_externa_observar_ou_humano():
    assert PLATAFORMA_WHATSAPP == "whatsapp"
    assert PLATAFORMA_WHATSAPP in PLATAFORMAS_EXTERNAS
    assert PLATAFORMA_WHATSAPP in PLATAFORMAS_LISTA
    # Fora da caixa: o cron do sync, o da IA e o manual não a veem.
    assert PLATAFORMA_WHATSAPP not in PLATAFORMAS
    assert PLATAFORMA_WHATSAPP not in PLATAFORMAS_CAIXA
    assert CANAIS_EXTERNOS[PLATAFORMA_WHATSAPP] == (CANAL_ZAP,)
    # Onda 2 (26/10/2026): `humano` junto com o envio; IA sozinha, nunca.
    assert MODOS_EXTERNOS[PLATAFORMA_WHATSAPP] == (MODO_OBSERVAR, MODO_HUMANO)
    assert SLA_HORAS[(PLATAFORMA_WHATSAPP, CANAL_ZAP)] == 24
    assert LIMITE_CARACTERES[(PLATAFORMA_WHATSAPP, CANAL_ZAP)] == 4096
    # As externas que já existiam não mudam.
    assert PLATAFORMAS_EXTERNAS[:3] == ("site", "instagram", "facebook")
    assert MODOS_EXTERNOS["instagram"] == ("observar", "humano")


def test_zap_so_pessoa_e_o_formato_antigo_recusado():
    # Onda 2 (26/10/2026): o Zap do número responde só por PESSOA — a régua
    # continua não vazia (a IA, a automática e a Caixa Humano ficam de fora);
    # o envio desvia antes dela (`enviar._destino_zap`).
    assert motivo_canal_sem_envio(CANAL_ZAP, PLATAFORMA_WHATSAPP, {}) == MOTIVO_ZAP_SO_PESSOA
    # P2 (trava de 05/10): Zap com a plataforma da venda, recusado para sempre.
    for plat in ("shopee", "ml", "tiktok", "amazon", "magalu"):
        assert motivo_canal_sem_envio(CANAL_ZAP, plat, None) == MOTIVO_ZAP_SEM_ENVIO, plat


def test_rotulos_dos_tipos_do_whatsapp():
    assert rotulo_tipo_plataforma("audio") == "[Áudio]"
    assert rotulo_tipo_plataforma("location") == "[Localização]"
    assert rotulo_tipo_plataforma("contacts") == "[Contato]"
    assert rotulo_tipo_plataforma("sticker") == "[Figurinha]"
    assert rotulo_tipo_plataforma("interactive") == "[Botão]"
    assert rotulo_tipo_plataforma("button") == "[Botão]"
    # O que ninguém conhece continua "[Mensagem]".
    assert rotulo_tipo_plataforma("tipo_que_a_meta_inventar") == "[Mensagem]"


def test_referencia_do_zap():
    assert canais_externos.PREFIXO_ZAP == "zap:"
    assert canais_externos.ref_do_zap(PNID) == f"zap:{PNID}"
    assert canais_externos.ref_do_zap(int(PNID)) == f"zap:{PNID}"
    assert canais_externos.ref_do_zap(f" {PNID} ") == f"zap:{PNID}"
    assert canais_externos.partes(f"zap:{PNID}") == ("zap", "whatsapp", PNID)
    for torta in ("", "abc", "1:2", "+5511999990000", "1" * 33, None):
        with pytest.raises(canais_externos.ReferenciaInvalida):
            canais_externos.ref_do_zap(torta)
    for torta in ("zap:", "zap:abc", "zap:1:2", "zap:" + "1" * 33, "zap :1", "whatsapp:1"):
        assert canais_externos.partes(torta) is None, torta
    # As referências de antes continuam iguais.
    assert canais_externos.partes("site:uranyx") == ("site", "site", "uranyx")
    assert canais_externos.partes("rede:facebook:139") == ("rede", "facebook", "139")
    assert canais_externos.modo_permitido("whatsapp", "observar")
    assert canais_externos.modo_permitido("whatsapp", "humano")  # Onda 2
    assert not canais_externos.modo_permitido("whatsapp", "copiloto")
    assert not canais_externos.modo_permitido("whatsapp", "auto")


# ── O canal do número ─────────────────────────────────────────────────────


async def test_garantir_canal_do_numero(db: AsyncSession):
    ref = canais_externos.ref_do_zap(PNID)
    c1 = await canais_externos.garantir_canal(
        db, externo_ref=ref, plataforma=PLATAFORMA_WHATSAPP, canal=CANAL_ZAP
    )
    await db.commit()
    # Sem nome gravado ainda: a linha diz que é um número de WhatsApp.
    assert canais_externos.nome_do_canal(c1) == f"WhatsApp · número {PNID}"
    c2 = await canais_externos.garantir_canal(
        db,
        externo_ref=ref,
        plataforma=PLATAFORMA_WHATSAPP,
        canal=CANAL_ZAP,
        nome="Charlots · (11) 90000-0000",
    )
    await db.commit()
    assert c1.id == c2.id
    assert (c2.plataforma, c2.canal, c2.modo, c2.status) == ("whatsapp", "zap", "observar", "novo")
    assert (c2.integration_id, c2.robo_perfil_id, c2.externo_ref) == (None, None, ref)
    assert canais_externos.eh_externo(c2)
    assert canais_externos.nome_do_canal(c2) == "Charlots · (11) 90000-0000"
    assert canais_externos.descricao_da_origem(c2).startswith("WhatsApp")
    assert canais_externos.pnid_do_canal(c2) == PNID
    assert canais_externos.site_do_canal(c2) is None
    # Um canal por número.
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AtendimentoCanal)
            .where(AtendimentoCanal.externo_ref == ref)
        )
    ) == 1
    # Caixa que não existe no WhatsApp, ou referência de outra plataforma.
    for kw in (
        {"externo_ref": ref, "plataforma": PLATAFORMA_WHATSAPP, "canal": "chat"},
        {"externo_ref": ref, "plataforma": "instagram", "canal": "comentario"},
        {"externo_ref": "site:charlots", "plataforma": PLATAFORMA_WHATSAPP, "canal": CANAL_ZAP},
    ):
        with pytest.raises(canais_externos.ReferenciaInvalida):
            await canais_externos.garantir_canal(db, **kw)


async def test_lista_filtra_pela_linha_do_numero(client, db, pessoa):
    canal = await canais_externos.garantir_canal(
        db,
        externo_ref=canais_externos.ref_do_zap(PNID),
        plataforma=PLATAFORMA_WHATSAPP,
        canal=CANAL_ZAP,
        nome="Charlots",
    )
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma=PLATAFORMA_WHATSAPP,
        canal_nome=CANAL_ZAP,
        externo_id=str(uuid4()),
        conta="+55 11 90000-0000",
        comprador_id="5511999990000",
        comprador_nome="Ana",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="wamid.teste-1",
        autor="cliente",
        texto="oi",
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
        tipo="texto",
        anexos=[],
        payload={},
        origem=None,
    )
    await db.commit()
    for params in (
        {"externo_ref": f"zap:{PNID}"},
        {"plataforma": "whatsapp"},
        {"plataforma": "whatsapp", "externo_ref": f"zap:{PNID}"},
    ):
        r = await client.get(f"{URL}/conversas", params=params)
        assert r.status_code == 200, (params, r.text)
        assert [i["id"] for i in r.json()["itens"]] == [str(conversa.id)], params
    r = await client.get(f"{URL}/conversas", params={"externo_ref": "zap:abc"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "externo_ref_invalido")


# ── As tabelas ────────────────────────────────────────────────────────────


async def _numero(
    db: AsyncSession, *, pnid: str = PNID, waba: str = "102290129340398", app_id: str = "999001"
) -> AtendimentoZapNumero:
    app = AtendimentoZapApp(
        app_id=app_id,
        nome="DaVinci Zap Teste",
        segredo_enc=cipher.encrypt_json(
            {"atual": "segredo-falso-de-teste", "anterior": None, "anterior_ate": None}
        ),
        verify_token_enc=cipher.encrypt_json({"verify_token": "verificacao-falsa"}),
        teste=True,
    )
    db.add(app)
    await db.flush()
    conta = AtendimentoZapConta(
        app_ref=app.id,
        waba_id=waba,
        token_enc=cipher.encrypt_json({"access_token": "token-falso", "tipo": "system_user"}),
    )
    db.add(conta)
    await db.flush()
    numero = AtendimentoZapNumero(
        conta_ref=conta.id, phone_number_id=pnid, display_phone_number="+55 11 90000-0000"
    )
    db.add(numero)
    await db.commit()
    return numero


async def test_tudo_nasce_desligado_e_os_segredos_ficam_cifrados(db: AsyncSession):
    numero = await _numero(db)
    await db.refresh(numero)
    assert numero.envio_ligado is False
    assert numero.teste is False
    assert numero.etapa == "preparando"
    assert numero.saude_status == "novo"
    assert (numero.destinos_teste, numero.registro_tentativas, numero.saude) == ([], [], {})
    assert numero.teto_dia is None and numero.pin_enc is None and numero.canal_id is None
    conta = await db.get(AtendimentoZapConta, numero.conta_ref)
    assert (conta.status, conta.token_nunca_vence, conta.inscrito_em) == ("novo", False, None)
    app = await db.get(AtendimentoZapApp, conta.app_ref)
    assert (app.ativo, app.teste) == (True, True)
    # Em repouso, cifrado: o banco não tem o texto do segredo nem do token.
    crus = (
        await db.execute(
            text(
                "SELECT a.segredo_enc, a.verify_token_enc, c.token_enc"
                " FROM atendimento_zap_apps a JOIN atendimento_zap_contas c ON c.app_ref = a.id"
            )
        )
    ).one()
    for cru in crus:
        assert b"falso" not in bytes(cru) and b"falsa" not in bytes(cru)
    assert cipher.decrypt_json(app.segredo_enc)["atual"] == "segredo-falso-de-teste"
    assert cipher.decrypt_json(conta.token_enc)["access_token"] == "token-falso"
    # A lista de teste e as tentativas de registro guardam o que se põe.
    numero.destinos_teste = ["5511999990000"]
    numero.registro_tentativas = [datetime(2026, 10, 19, 12, tzinfo=UTC)]
    await db.commit()
    await db.refresh(numero)
    assert numero.destinos_teste == ["5511999990000"]
    assert numero.registro_tentativas == [datetime(2026, 10, 19, 12, tzinfo=UTC)]


async def test_checks_das_colunas_de_estado(db: AsyncSession):
    # O id antes dos rollbacks (depois deles o objeto expira).
    numero_id = (await _numero(db)).id
    for tabela, coluna, valor in (
        ("atendimento_zap_numeros", "etapa", "no_duoke"),
        ("atendimento_zap_numeros", "saude_status", "verde"),
        ("atendimento_zap_contas", "status", "quebrado"),
    ):
        with pytest.raises(IntegrityError, match=f"ck_{tabela}_{coluna}"):
            await db.execute(text(f"UPDATE {tabela} SET {coluna} = :v"), {"v": valor})  # noqa: S608
        await db.rollback()
    # Os valores do desenho passam.
    for etapa in ("teste", "aguardando_duoke", "migrando", "ativo", "pausado", "desligado"):
        await db.execute(
            text("UPDATE atendimento_zap_numeros SET etapa = :e WHERE id = :i"),
            {"e": etapa, "i": numero_id},
        )
    await db.commit()
    db.add(AtendimentoZapContato(numero_ref=numero_id, wa_id="5511999990000", opt_out_tipo="tudo"))
    with pytest.raises(IntegrityError, match="ck_atendimento_zap_contatos_opt_out_tipo"):
        await db.commit()
    await db.rollback()
    db.add(AtendimentoZapEvento(chave="m:wamid.x", tipo="outro"))
    with pytest.raises(IntegrityError, match="ck_atendimento_zap_eventos_tipo"):
        await db.commit()
    await db.rollback()
    db.add(AtendimentoZapEvento(chave="m:wamid.x", tipo="mensagem", resultado="perdido"))
    with pytest.raises(IntegrityError, match="ck_atendimento_zap_eventos_resultado"):
        await db.commit()
    await db.rollback()
    db.add(AtendimentoZapMidia(numero_ref=numero_id, direcao="ida"))
    with pytest.raises(IntegrityError, match="ck_atendimento_zap_midias_direcao"):
        await db.commit()
    await db.rollback()


async def test_um_contato_por_bsuid_e_por_telefone_em_cada_numero(db: AsyncSession):
    numero_id = (await _numero(db)).id
    outro_id = (
        await _numero(db, pnid="106540352242999", waba="102290129340999", app_id="999002")
    ).id
    db.add_all(
        [
            AtendimentoZapContato(numero_ref=numero_id, bsuid="BR.1", wa_id_norm="5511999990000"),
            # Sem BSUID nem telefone normalizado: o parcial não vale para NULL.
            AtendimentoZapContato(numero_ref=numero_id, wa_id="5511999990001"),
            AtendimentoZapContato(numero_ref=numero_id, wa_id="5511999990002"),
            # O mesmo cliente em OUTRO número é outro contato.
            AtendimentoZapContato(numero_ref=outro_id, bsuid="BR.1", wa_id_norm="5511999990000"),
        ]
    )
    await db.commit()
    db.add(AtendimentoZapContato(numero_ref=numero_id, bsuid="BR.1"))
    with pytest.raises(IntegrityError, match="uq_atendimento_zap_contatos_bsuid"):
        await db.commit()
    await db.rollback()
    db.add(AtendimentoZapContato(numero_ref=numero_id, wa_id_norm="5511999990000"))
    with pytest.raises(IntegrityError, match="uq_atendimento_zap_contatos_wa_id_norm"):
        await db.commit()
    await db.rollback()
    # Número com contato não se apaga (RESTRICT): o histórico do cliente fica.
    with pytest.raises(IntegrityError, match="fk_atendimento_zap_contatos_numero"):
        await db.execute(
            text("DELETE FROM atendimento_zap_numeros WHERE id = :i"), {"i": numero_id}
        )
    await db.rollback()


async def test_evento_unico_pela_chave_e_a_fila(db: AsyncSession):
    agora = datetime.now(UTC)

    def linha(chave: str, tipo: str, **extra) -> dict:
        base = {"phone_number_id": PNID, "payload": {}, "resultado": None, "processado_em": None}
        return {"chave": chave, "tipo": tipo, **base, **extra}

    linhas = [
        linha("m:wamid.1", "mensagem", payload={"a": 1}),
        linha("s:wamid.1:delivered", "status"),
        linha("j:abc", "ignorado", resultado="json_invalido", processado_em=agora),
    ]
    for _ in range(2):
        # A reentrega da Meta para no UNIQUE da `chave`.
        await db.execute(
            pg_insert(AtendimentoZapEvento)
            .values(linhas)
            .on_conflict_do_nothing(index_elements=["chave"])
        )
        await db.commit()
    eventos = (
        (await db.execute(select(AtendimentoZapEvento).order_by(AtendimentoZapEvento.id)))
        .scalars()
        .all()
    )
    assert [e.chave for e in eventos] == ["m:wamid.1", "s:wamid.1:delivered", "j:abc"]
    assert eventos[0].id < eventos[1].id < eventos[2].id
    assert (eventos[0].tentativas, eventos[0].proxima_em, eventos[0].processado_em) == (
        0,
        None,
        None,
    )
    assert eventos[0].recebido_em is not None and eventos[1].payload == {}
    # A fila: o evento novo (`proxima_em` NULL) é pego; o adiado, só na hora.
    eventos[1].proxima_em = agora + timedelta(minutes=2)
    await db.commit()
    ev = AtendimentoZapEvento
    fila = select(ev.chave).where(
        ev.processado_em.is_(None),
        ev.proxima_em.is_(None) | (ev.proxima_em <= func.now()),
    )
    assert (await db.execute(fila)).scalars().all() == ["m:wamid.1"]


async def test_uma_midia_por_media_id_em_cada_numero(db: AsyncSession):
    numero = await _numero(db)
    db.add(AtendimentoZapMidia(numero_ref=numero.id, direcao="entrada", media_id="m1"))
    # A saída ainda sem upload (media_id NULL) pode repetir.
    db.add(AtendimentoZapMidia(numero_ref=numero.id, direcao="saida"))
    db.add(AtendimentoZapMidia(numero_ref=numero.id, direcao="saida"))
    await db.commit()
    m = (
        await db.execute(select(AtendimentoZapMidia).where(AtendimentoZapMidia.media_id == "m1"))
    ).scalar_one()
    assert (m.status, m.tentativas, m.caminho) == ("pendente", 0, None)
    db.add(AtendimentoZapMidia(numero_ref=numero.id, direcao="entrada", media_id="m1"))
    with pytest.raises(IntegrityError, match="uq_atendimento_zap_midias_numero_ref_media_id"):
        await db.commit()
    await db.rollback()
