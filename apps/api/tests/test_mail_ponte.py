"""A PONTE: o e-mail da Central de e-mail → a conversa do /atendimento (08/10/2026).

Postgres local de verdade. O "mundo" (`cena`) tem as duas caixas como em
produção — a GERAL da empresa (lida pelo nosso conector, todas as pastas) e a
GOSLIN privada (o agente IMAP dele, só a Entrada) — e o cadastro das lojas com
`store_info.email` só com a parte antes do @. O e-mail entra na Central como o
`ingest` dela grava (cifrado), e a ponte decide.

O que não pode falhar (a crítica de 08/10):
  • a caixa privada nunca vaza: só o e-mail de ALIAS DE LOJA passa, e o resto
    não deixa nada em claro (nem pasta, nem alias, nem hash);
  • e-mail de segurança (código, senha, acesso) nunca vira conversa;
  • link de acesso e código saem do texto que a equipe vê;
  • a ponte nunca grava 2 mensagens do mesmo e-mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    Integration,
    MailAttachment,
    MailMailbox,
    MailMessage,
    MailOutbox,
    Marca,
    MarcaEmail,
    StoreInfo,
    User,
    UserRole,
)
from app.models.enums import IntegrationPlatform
from app.models.mail_atendimento import (
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
    MailOutboxMeta,
)
from app.security.cipher import encrypt_bytes, encrypt_json
from app.services import mail_central
from app.services.atendimento.constantes import CHAVE_VEZ_DA_LOJA
from app.services.mail_atendimento import ponte

AGORA = datetime.now(UTC)
GERAL = "geral@tuta.com"
GOSLIN = "goslin@tuta.com"
TOKEN_GERAL = "token-da-geral-para-teste-0123456789"  # noqa: S105 — de teste
TOKEN_GOSLIN = "token-da-goslin-para-teste-012345678"  # noqa: S105 — de teste
ALIASES_GERAL = [
    "21max@tuta.com",
    "16tr@tuta.com",
    "mike14@tuta.com",
    "hans21@tuta.com",
    "adm@poofy.com.br",
    "sac@uranyx.com.br",
    "atacado@uranyx.com.br",
    "duvidas@uranyx.com.br",
]
ALIASES_GOSLIN = ["mia30@tuta.com"]


@dataclass
class Cena:
    dono: User
    geral: MailMailbox
    goslin: MailMailbox
    ml: Integration
    shopee: Integration
    amazon: Integration
    oliveira_ml: Integration
    loja_ml: StoreInfo
    loja_oliveira_ml: StoreInfo
    marca: Marca


async def _integ(db, user, plataforma: IntegrationPlatform, nome: str) -> Integration:
    i = Integration(user_id=user.id, platform=plataforma, name=nome, credentials=encrypt_json({}))
    db.add(i)
    await db.flush()
    return i


async def _loja(db, user, plataforma: str, email: str, integ, nome: str) -> StoreInfo:
    s = StoreInfo(
        user_id=user.id,
        platform=plataforma,
        account_name=nome,
        email=email,
        integration_id=integ.id if integ else None,
    )
    db.add(s)
    await db.flush()
    return s


def _caixa(dono: User, label: str, principal: str, aliases: list[str], token: str) -> MailMailbox:
    return MailMailbox(
        id=uuid4(),
        owner_user_id=dono.id,
        label=label,
        config_enc=encrypt_json({"address": principal, "aliases": [principal, *aliases]}),
        agent_token_hash=mail_central.token_hash(token),
        send_enabled=False,
        agent_can_send=False,
        state="online",
        last_seen_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    return s


@pytest.fixture
async def cena(db: AsyncSession, make_user) -> Cena:
    dono = await make_user(role=UserRole.ADMIN)
    ml = await _integ(db, dono, IntegrationPlatform.ML, "barbosa-ml")
    shopee = await _integ(db, dono, IntegrationPlatform.SHOPEE, "barbosa-shopee")
    amazon = await _integ(db, dono, IntegrationPlatform.AMAZON, "mike-amazon")
    oliveira_ml = await _integ(db, dono, IntegrationPlatform.ML, "oliveira-ml")
    oliveira_shopee = await _integ(db, dono, IntegrationPlatform.SHOPEE, "oliveira-shopee")
    loja_ml = await _loja(db, dono, "mercadolivre", "21max", ml, "Barbosa")
    await _loja(db, dono, "shopee", "21max", shopee, "Barbosa")
    await _loja(db, dono, "mercadolivre", "16tr", None, "JLAS2")
    await _loja(db, dono, "amazon", "mike14", amazon, "Mike")
    loja_oliveira_ml = await _loja(db, dono, "mercadolivre", "mia30", oliveira_ml, "Oliveira")
    await _loja(db, dono, "shopee", "mia30", oliveira_shopee, "Oliveira")
    marca = Marca(
        nome="Uranyx", slug="uranyx", dominio_br="uranyx.com.br", site="https://uranyx.com.br"
    )
    db.add(marca)
    await db.flush()
    for tipo in ("sac", "atacado", "duvidas"):
        db.add(MarcaEmail(marca_id=marca.id, tipo=tipo, email=f"{tipo}@uranyx.com.br"))
    geral = _caixa(dono, "Geral — Tuta", GERAL, ALIASES_GERAL, TOKEN_GERAL)
    goslin = _caixa(dono, "Goslin — Tuta", GOSLIN, ALIASES_GOSLIN, TOKEN_GOSLIN)
    db.add_all([geral, goslin])
    await db.flush()
    desde = AGORA - timedelta(days=1)
    db.add(
        MailMailboxSettings(
            mailbox_id=geral.id,
            visibilidade="empresa",
            ponte_ligada=True,
            ponte_desde=desde,
            ponte_so_aliases_de_loja=False,
        )
    )
    db.add(
        MailMailboxSettings(
            mailbox_id=goslin.id,
            visibilidade="privada",
            ponte_ligada=True,
            ponte_desde=desde,
            ponte_so_aliases_de_loja=True,
        )
    )
    await db.commit()
    return Cena(
        dono=dono,
        geral=geral,
        goslin=goslin,
        ml=ml,
        shopee=shopee,
        amazon=amazon,
        oliveira_ml=oliveira_ml,
        loja_ml=loja_ml,
        loja_oliveira_ml=loja_oliveira_ml,
        marca=marca,
    )


_SEQ = iter(range(1, 10**6))


async def entrar(
    db: AsyncSession,
    caixa: MailMailbox,
    *,
    pasta: str = "problema ml",
    de: str = "maria@gmail.com",
    de_nome: str = "Maria",
    para: list[str] | None = None,
    cc: list[str] | None = None,
    assunto: str = "Pergunta sobre o pedido",
    corpo: str = "Quando chega?",
    message_id: str | None = "auto",
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    reply_to: str | None = None,
    direcao: str = "inbound",
    recebido_em: datetime | None = None,
    anexos: list[tuple[str, str, bytes]] | None = None,
    **extra,
) -> UUID:
    """Um e-mail na Central como o `ingest` dela grava (o conteúdo cifrado)."""
    n = next(_SEQ)
    conteudo = {
        "folder": pasta,
        "subject": assunto,
        "from_address": de,
        "from_name": de_nome,
        "to": para if para is not None else ["21max@tuta.com"],
        "cc": cc or [],
        "reply_to": reply_to,
        "message_id": f"<msg{n}@mail.teste>" if message_id == "auto" else (message_id or ""),
        "in_reply_to": in_reply_to,
        "references": references or [],
        "text": corpo,
        **extra,
    }
    message = MailMessage(
        id=uuid4(),
        mailbox_id=caixa.id,
        source_id=f"tuta:L{n}/E{n}",
        direction=direcao,
        received_at=recebido_em or AGORA - timedelta(minutes=5),
        content_enc=encrypt_json(conteudo),
        attachment_count=len(anexos or []),
    )
    db.add(message)
    await db.flush()
    for nome, tipo, dados in anexos or []:
        db.add(
            MailAttachment(
                message_id=message.id,
                metadata_enc=encrypt_json({"filename": nome, "content_type": tipo}),
                content_enc=encrypt_bytes(dados),
                size=len(dados),
            )
        )
    await db.commit()
    return message.id


async def rodar(db: AsyncSession) -> dict:
    return await ponte.rodar(db)


async def meta_de(db: AsyncSession, message_id: UUID) -> MailMessageMeta | None:
    return (
        await db.execute(
            select(MailMessageMeta)
            .where(MailMessageMeta.message_id == message_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def conversa(db: AsyncSession, conversa_id: UUID) -> AtendimentoConversa:
    return (
        await db.execute(
            select(AtendimentoConversa)
            .where(AtendimentoConversa.id == conversa_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def mensagens(db: AsyncSession, conversa_id: UUID) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def pedido_existe(db, integ: Integration, numero: str, plataforma: str = "ml") -> None:
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id, plataforma=plataforma, comprador_id="c1", pedido=numero
        )
    )
    await db.commit()


async def contar(db, modelo) -> int:
    return int(await db.scalar(select(func.count()).select_from(modelo)) or 0)


# ─────────────── desligada e o corte ───────────────


async def test_ponte_desligada_nem_decifra(db, cena):
    cfg = await db.get(MailMailboxSettings, cena.geral.id)
    cfg.ponte_ligada = False
    await db.commit()
    await entrar(db, cena.geral)
    assert (await rodar(db))["processados"] == 0
    assert await contar(db, MailMessageMeta) == 0
    assert await contar(db, AtendimentoConversa) == 0


async def test_email_de_antes_do_corte_nunca_entra(db, cena):
    velho = await entrar(db, cena.geral, recebido_em=AGORA - timedelta(days=3))
    novo = await entrar(db, cena.geral)
    await rodar(db)
    assert await meta_de(db, velho) is None
    assert (await meta_de(db, novo)).estado == "sem_vinculo"


async def test_email_antigo_nao_toma_a_vez_do_novo(db, cena):
    """O SQL da volta já corta pelo `ponte_desde`: o antigo nunca ocupa o lote."""
    for _ in range(3):
        await entrar(db, cena.geral, recebido_em=AGORA - timedelta(days=5))
    novo = await entrar(db, cena.geral)
    resumo = await ponte.rodar(db, limite=2)
    assert resumo["processados"] == 1
    assert (await meta_de(db, novo)).estado == "sem_vinculo"


# ─────────────── loja, pedido e conversa ───────────────


async def test_problema_ml_com_pedido_entra_na_conversa_da_loja(db, cena):
    await pedido_existe(db, cena.ml, "2000012345678901")
    mid = await entrar(
        db,
        cena.geral,
        assunto="Reclamação no pedido 2000012345678901",
        corpo="Chegou quebrado.\n\nEm 07/10/2026, Loja <21max@tuta.com> escreveu:\n> Olá",
    )
    resumo = await rodar(db)
    assert resumo["processados"] == 1 and resumo["erros"] == 0
    meta = await meta_de(db, mid)
    assert meta.estado == "gravado" and meta.vinculado_por == "pedido"
    assert (meta.integration_id, meta.store_info_id) == (cena.ml.id, cena.loja_ml.id)
    assert (meta.alias_recebido, meta.plataforma, meta.finalidade) == (
        "21max@tuta.com",
        "ml",
        "problema",
    )
    assert meta.pedido_marketplace == "2000012345678901"
    assert meta.mid_hash and meta.tuta_id and meta.regras_versao == 1
    c = await conversa(db, meta.conversa_id)
    assert (c.canal, c.plataforma, c.integration_id) == ("email", "ml", cena.ml.id)
    assert c.dados["fonte"] == "tuta" and c.dados["mail"]["destaque"] is True
    assert c.dados["mail"]["mailbox_id"] == str(cena.geral.id)
    assert c.pedido_marketplace == "2000012345678901" and c.comprador_id == "maria@gmail.com"
    assert c.aguardando_resposta is True
    ms = await mensagens(db, c.id)
    assert len(ms) == 1 and ms[0].id == meta.mensagem_id
    # Só o texto NOVO (sem a citação) vai para a conversa.
    assert ms[0].texto == "Chegou quebrado." and ms[0].autor == "cliente"
    assert ms[0].payload["mail"]["message_id"] == str(mid)
    assert ponte.e_conversa_da_ponte(c)


async def test_sem_pedido_e_sem_vinculo_e_vendas_e_so_historico(db, cena):
    sem = await entrar(db, cena.geral, pasta="mensagens ml")
    vendas = await entrar(
        db,
        cena.geral,
        pasta="vendas ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto="Você vendeu!",
    )
    await rodar(db)
    assert (await meta_de(db, sem)).estado == "sem_vinculo"
    m = await meta_de(db, vendas)
    c = await conversa(db, m.conversa_id)
    assert c.sem_resposta_necessaria is True and c.aguardando_resposta is False
    assert (await mensagens(db, c.id))[0].autor == "sistema"


async def test_sem_loja_fica_na_fila_sem_conversa(db, cena):
    # hans21 não tem cadastro: fila "sem loja", sem conversa.
    sem_cad = await entrar(db, cena.geral, para=["hans21@tuta.com"])
    await rodar(db)
    b = await meta_de(db, sem_cad)
    assert (b.estado, b.motivo) == ("sem_loja", "alias_sem_cadastro")
    assert b.conversa_id is None
    assert await contar(db, AtendimentoConversa) == 0


async def test_loja_sem_integracao_entra_na_conversa_da_ficha(db, cena):
    # 16tr é da JLAS2 (ficha SEM FK e sem integração com o mesmo nome): a loja é
    # a ficha — crítica de 08/10 (Temu, AliExpress, as lojas da Goslin).
    mid = await entrar(db, cena.geral, para=["16tr@tuta.com"], pasta="problema ml")
    outra = await entrar(db, cena.geral, para=["16tr@tuta.com"], pasta="problema ml")
    await rodar(db)
    a, b = await meta_de(db, mid), await meta_de(db, outra)
    ficha = await db.scalar(select(StoreInfo).where(StoreInfo.email == "16tr"))
    assert (a.estado, a.integration_id, a.store_info_id) == ("sem_vinculo", None, ficha.id)
    assert any(x["codigo"] == "loja_sem_integracao" for x in a.alertas)
    c = await conversa(db, a.conversa_id)
    assert (c.integration_id, c.plataforma, c.conta) == (None, "ml", "JLAS2")
    assert c.dados["mail"]["store_info_id"] == str(ficha.id)
    assert c.externo_id.startswith(f"mail-loja:{ficha.id}:")
    # Outro e-mail da mesma ficha, sem fio nem pedido: outra conversa da MESMA loja.
    assert b.store_info_id == ficha.id and b.conversa_id != a.conversa_id
    # Uma ficha de OUTRA loja nunca cai na conversa desta.
    await _loja(db, cena.dono, "mercadolivre", "hans21", None, "Hans")
    await db.commit()
    terceiro = await entrar(db, cena.geral, para=["hans21@tuta.com"], pasta="problema ml")
    await rodar(db)
    t = await meta_de(db, terceiro)
    assert t.conversa_id not in (a.conversa_id, b.conversa_id)


async def test_ficha_sem_fk_acha_a_integracao_pelo_par(db, cena):
    # Produção: só 13 das 83 fichas têm o FK. A integração " JLAS2" (com espaço)
    # do ML casa com a ficha "JLAS2" do ML pelo par (nome, plataforma).
    jlas2 = await _integ(db, cena.dono, IntegrationPlatform.ML, " JLAS2 ")
    await db.commit()
    mid = await entrar(db, cena.geral, para=["16tr@tuta.com"], pasta="problema ml")
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.integration_id == jlas2.id and m.estado == "sem_vinculo"
    assert not any(x["codigo"] == "loja_sem_integracao" for x in m.alertas)
    c = await conversa(db, m.conversa_id)
    assert c.integration_id == jlas2.id


async def test_alias_interno_nunca_vai_para_a_fila(db, cena):
    mid = await entrar(db, cena.geral, pasta="INBOX", para=["adm@poofy.com.br"])
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo) == ("ignorado", "alias_interno")


async def test_pasta_que_so_se_conta_nao_leva_o_corpo(db, cena):
    fin = await entrar(db, cena.geral, pasta="financeiro")
    lixo = await entrar(db, cena.geral, pasta="Trash")
    await rodar(db)
    for mid in (fin, lixo):
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo) == ("ignorado", "pasta_so_contar")
        # Nada do e-mail em claro: nem alias, nem hash, nem loja.
        assert m.alias_recebido is None and m.mid_hash is None and m.integration_id is None
    pasta = await db.scalar(
        select(MailFolder).where(
            MailFolder.mailbox_id == cena.geral.id, MailFolder.chave == "financeiro"
        )
    )
    assert pasta.ler == "so_contar" and pasta.revisada is False


async def test_entrada_alias_de_duas_plataformas_pede_a_loja(db, cena):
    # Goslin (IMAP, só a Entrada): mia30 é da Oliveira no ML e na Shopee. O
    # cliente (gmail) não diz a plataforma: a pessoa escolhe.
    mid = await entrar(db, cena.goslin, pasta="INBOX", para=["mia30@tuta.com"])
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo) == ("sem_loja", "entrada_sem_plataforma")
    assert {s["plataforma"] for s in m.sugestoes} == {"ml", "shopee"}
    # O aviso OFICIAL do ML diz a plataforma: vai para a Oliveira do ML.
    aviso = await entrar(
        db,
        cena.goslin,
        pasta="INBOX",
        para=["mia30@tuta.com"],
        de="aviso@mercadolivre.com.br",
        de_nome="Mercado Livre",
    )
    await rodar(db)
    a = await meta_de(db, aviso)
    assert a.integration_id == cena.oliveira_ml.id and a.estado in ("gravado", "sem_vinculo")


async def test_resposta_do_cliente_na_entrada_acha_a_conversa_pelo_fio(db, cena):
    primeiro = await entrar(
        db, cena.goslin, pasta="INBOX", para=["mia30@tuta.com"], message_id="<um@cliente.com>"
    )
    await rodar(db)
    m1 = await meta_de(db, primeiro)
    assert m1.estado == "sem_loja"
    # A pessoa escolhe a loja (fila "sem loja"): a conversa nasce.
    rota = ponte.rotear.Rota(
        alias="mia30@tuta.com",
        store_info_id=cena.loja_oliveira_ml.id,
        integration_id=cena.oliveira_ml.id,
        plataforma="ml",
    )
    assert await ponte.reprocessar(db, primeiro, rota_forcada=rota) == "sem_vinculo"
    await db.commit()
    m1 = await meta_de(db, primeiro)
    assert m1.vinculado_por == "manual"
    resposta = await entrar(
        db,
        cena.goslin,
        pasta="INBOX",
        para=["mia30@tuta.com"],
        in_reply_to="<um@cliente.com>",
        references=["<um@cliente.com>"],
        corpo="Alguma novidade?",
    )
    await rodar(db)
    m2 = await meta_de(db, resposta)
    assert m2.conversa_id == m1.conversa_id and m2.vinculado_por == "referencia"
    assert len(await mensagens(db, m1.conversa_id)) == 2


# ─────────────── a caixa PRIVADA nunca vaza ───────────────


async def test_caixa_privada_so_leva_o_email_de_alias_de_loja(db, cena):
    pessoal = await entrar(
        db, cena.goslin, pasta="INBOX", para=[GOSLIN], assunto="Boleto", corpo="pessoal"
    )
    nao_cadastrado = await entrar(db, cena.goslin, pasta="INBOX", para=["ravi46@tuta.com"])
    await rodar(db)
    for mid in (pessoal, nao_cadastrado):
        m = await meta_de(db, mid)
        assert (m.estado, m.motivo) == ("privado", "nao_e_alias_de_loja")
        # Nada em claro: nem pasta, nem alias, nem hash, nem loja, nem conversa.
        assert (m.folder_id, m.alias_recebido, m.mid_hash, m.integration_id, m.conversa_id) == (
            None,
            None,
            None,
            None,
            None,
        )
    # A pasta da caixa privada nem vira linha.
    assert await contar(db, MailFolder) == 0
    assert await contar(db, AtendimentoConversa) == 0


async def test_duplicado_nunca_contra_email_privado(db, cena):
    # O mesmo e-mail na Goslin (pessoal, privado) e na geral (para uma loja).
    await entrar(db, cena.goslin, pasta="INBOX", para=[GOSLIN], message_id="<mesmo@x.com>")
    await rodar(db)
    na_geral = await entrar(db, cena.geral, message_id="<mesmo@x.com>")
    await rodar(db)
    assert (await meta_de(db, na_geral)).estado == "sem_vinculo"


async def test_duplicado_nunca_contra_email_que_saiu_da_equipe(db, cena):
    # Sem loja → alguém tirou da fila: não está mais com a equipe.
    primeiro = await entrar(db, cena.geral, para=["hans21@tuta.com"], message_id="<x1@x.com>")
    await rodar(db)
    meta = await meta_de(db, primeiro)
    meta.estado = "ignorado"
    meta.motivo = "por_pessoa"
    await db.commit()
    copia = await entrar(
        db, cena.goslin, pasta="INBOX", para=["mia30@tuta.com"], message_id="<x1@x.com>"
    )
    await rodar(db)
    assert (await meta_de(db, copia)).estado == "sem_loja"


async def test_duplicado_do_que_a_ponte_ja_levou_a_equipe(db, cena):
    primeiro = await entrar(db, cena.geral, message_id="<repete@x.com>")
    await rodar(db)
    copia = await entrar(
        db, cena.goslin, pasta="INBOX", para=["mia30@tuta.com"], message_id="<repete@x.com>"
    )
    await rodar(db)
    m = await meta_de(db, copia)
    assert (m.estado, m.duplicado_de) == ("duplicado", primeiro)
    assert await contar(db, AtendimentoMensagem) == 1


# ─────────────── segurança e o que a equipe vê ───────────────


async def test_email_de_seguranca_nunca_vira_conversa(db, cena):
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="no-reply@mercadolivre.com.br",
        assunto="Seu código de verificação",
        corpo="Use 482913 para entrar na sua conta.",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo, m.codigo_mascarado) == ("seguranca", "codigo_ou_acesso", True)
    assert m.conversa_id is None and m.mid_hash is None and m.alias_recebido is None
    assert await contar(db, AtendimentoMensagem) == 0


async def test_cliente_que_fala_de_senha_entra_e_o_link_some(db, cena):
    # O cliente escrevendo para a CAIXA DE SITE (sac@ da marca): é atendimento.
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["sac@uranyx.com.br"],
        assunto="Esqueci minha senha",
        corpo="Não consigo entrar no app. O link https://app.uranyx.com.br/redefinir-senha?t=9 "
        "não abre. Veja também https://uranyx.com.br/produto/capa",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado" and m.conversa_id is not None
    texto = (await db.get(AtendimentoMensagem, m.mensagem_id)).texto
    # Fala de senha: TODOS os links saem (o curto de reset não tem cara de acesso).
    assert "redefinir-senha" not in texto and "produto/capa" not in texto
    assert texto.count("[link de acesso removido]") == 2


@pytest.mark.parametrize(
    ("de", "assunto", "corpo"),
    [
        (
            "seguranca@bling.com.br",
            "Redefinição de senha",
            "Clique para redefinir sua senha: https://www.bling.com.br/r/AbCdEf",
        ),
        ("contato@melhorenvio.com.br", "Acesso", "Sua nova senha de acesso é: Ab12cd34"),
        ("security@facebookmail.com", "Novo login", "Detectamos um novo login na sua conta."),
        ("maria@gmail.com", "Ajuda", "Esqueci minha senha do app, como faço?"),
    ],
)
async def test_senha_ou_acesso_de_remetente_desconhecido_no_login_da_loja(
    db, cena, de, assunto, corpo
):
    # A crítica de 08/10 (BLOQUEIO): 21max é o LOGIN das lojas Barbosa; o
    # remetente fora da lista de plataformas não é "pessoa" por padrão.
    mid = await entrar(db, cena.geral, pasta="mensagens ml", de=de, assunto=assunto, corpo=corpo)
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo) == ("seguranca", "codigo_ou_acesso")
    assert m.conversa_id is None and m.mid_hash is None and m.alias_recebido is None
    assert await contar(db, AtendimentoMensagem) == 0


async def test_no_login_da_loja_a_regra_e_estrita_mesmo_no_fio_da_equipe(db, cena):
    # 21max é LOGIN das lojas Barbosa: nem a resposta no fio que a equipe já
    # tem afrouxa a regra (um reset ali tomaria a conta da loja).
    primeiro = await entrar(db, cena.geral, message_id="<fio21@cliente.com>")
    await rodar(db)
    assert (await meta_de(db, primeiro)).conversa_id is not None
    resposta = await entrar(
        db,
        cena.geral,
        assunto="Re: Pergunta",
        corpo="Esqueci minha senha, me ajudem a redefinir sua senha.",
        in_reply_to="<fio21@cliente.com>",
    )
    await rodar(db)
    assert (await meta_de(db, resposta)).estado == "seguranca"


async def test_cliente_respondendo_num_fio_da_equipe_pode_falar_de_senha(db, cena):
    # hans21 não é login de loja: o primeiro e-mail fica na fila "sem loja" (da
    # equipe); a resposta do cliente NO MESMO FIO falando de senha é atendimento.
    primeiro = await entrar(db, cena.geral, para=["hans21@tuta.com"], message_id="<um@cliente.com>")
    await rodar(db)
    assert (await meta_de(db, primeiro)).estado == "sem_loja"
    resposta = await entrar(
        db,
        cena.geral,
        para=["hans21@tuta.com"],
        assunto="Re: Pergunta",
        corpo="Esqueci minha senha do site, podem ajudar?",
        in_reply_to="<um@cliente.com>",
    )
    sem_fio = await entrar(
        db, cena.geral, para=["hans21@tuta.com"], assunto="Esqueci minha senha", corpo="Oi"
    )
    await rodar(db)
    assert (await meta_de(db, resposta)).estado == "sem_loja"
    assert (await meta_de(db, sem_fio)).estado == "seguranca"


async def test_link_de_acesso_e_codigo_saem_do_texto_da_conversa(db, cena):
    mid = await entrar(
        db,
        cena.geral,
        corpo=(
            "Oi, veja: https://www.mercadolivre.com.br/vendas/2000012345678901/detalhe\n"
            "e https://loja.com/login?token=abcdef0123456789abcdef"
        ),
    )
    await rodar(db)
    m = await meta_de(db, mid)
    texto = (await db.get(AtendimentoMensagem, m.mensagem_id)).texto
    assert "token=" not in texto and "[link de acesso removido]" in texto
    assert "vendas/2000012345678901/detalhe" in texto
    payload = (await db.get(AtendimentoMensagem, m.mensagem_id)).payload["mail"]
    assert payload["links_removidos"] == 1


async def test_remetente_que_imita_a_plataforma_e_suspeito_e_nao_liga_ao_pedido(db, cena):
    await pedido_existe(db, cena.ml, "2000012345678901")
    mid = await entrar(
        db,
        cena.geral,
        de="suporte@mercadolivre-br.com",
        de_nome="Mercado Livre",
        assunto="Pedido 2000012345678901 bloqueado",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.suspeito and "dominio_parecido" in m.suspeito_motivos
    assert m.pedido_marketplace is None and m.estado == "sem_vinculo"
    assert any(a["codigo"] == "suspeito_sem_vinculo" for a in m.alertas)


# ─────────────── aviso da plataforma com API ───────────────


async def test_aviso_do_ml_vai_para_a_conversa_da_api_sem_pendencia(db, cena):
    api = AtendimentoConversa(
        integration_id=cena.ml.id,
        plataforma="ml",
        canal="pos_venda",
        externo_id="pack-1",
        pedido_marketplace="2000012345678901",
        situacao="respondida",
    )
    db.add(api)
    await db.commit()
    await pedido_existe(db, cena.ml, "2000012345678901")
    mid = await entrar(
        db,
        cena.geral,
        pasta="mensagens ml",
        de="nao-responder@mercadolivre.com.br",
        de_nome="Mercado Livre",
        assunto="Nova mensagem no pedido 2000012345678901",
        corpo="O comprador mandou uma mensagem.",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.vinculado_por, m.conversa_id) == ("gravado", "aviso_api", api.id)
    c = await conversa(db, api.id)
    assert c.aguardando_resposta is False
    msg = (await mensagens(db, api.id))[-1]
    assert msg.autor == "sistema" and msg.payload["mail"]["aviso_api"] is True
    # Nenhuma conversa de e-mail nova.
    assert await db.scalar(select(func.count()).where(AtendimentoConversa.canal == "email")) == 0


# ─────────────── RF6: chamados dos sites ───────────────


async def test_rf6_formulario_vira_chamado_com_protocolo_e_tipo(db, cena):
    mid = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="sac@uranyx.com.br",
        de_nome="Site Uranyx",
        para=["sac@uranyx.com.br"],
        assunto="[US-26-0014] Troca de produto",
        corpo="Nome: Maria\nE-mail: Maria.Silva@Gmail.com\nMensagem: quero trocar",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.protocolo, m.tipo_caixa, m.marca_id) == (
        "gravado",
        "US-26-0014",
        "sac",
        cena.marca.id,
    )
    c = await conversa(db, m.conversa_id)
    assert (c.plataforma, c.integration_id, c.externo_id) == (
        "site",
        None,
        "mail-protocolo:US-26-0014",
    )
    assert c.comprador_id == "maria.silva@gmail.com"
    assert c.dados["mail"]["caixa_rotulo"] == "SAC" and c.dados["mail"]["marca"] == "uranyx"
    assert c.aguardando_resposta is True
    assert (await mensagens(db, c.id))[0].autor == "cliente"


async def test_rf6_protocolo_atacado_numa_caixa_sac_vale_o_protocolo(db, cena):
    mid = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="sac@uranyx.com.br",
        para=["sac@uranyx.com.br"],
        assunto="[UA-26-0002] Pedido de atacado",
        corpo="E-mail: loja@revenda.com.br",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.tipo_caixa == "atacado"
    assert any(a["codigo"] == "tipo_x_caixa" for a in m.alertas)


async def test_endereco_de_pessoa_no_dominio_da_marca_nao_vira_chamado(db, cena):
    # O contador para gabrieli@ do domínio da marca, na pasta "*uranyx": não é
    # caixa de site — fila "sem loja" (só quem mexe), nunca conversa de site.
    geral = await db.get(MailMailbox, cena.geral.id)
    config = {"address": GERAL, "aliases": [GERAL, *ALIASES_GERAL, "gabrieli@uranyx.com.br"]}
    geral.config_enc = encrypt_json(config)
    await db.commit()
    mid = await entrar(
        db,
        cena.geral,
        pasta="*uranyx",
        de="contador@escritorio.com.br",
        para=["gabrieli@uranyx.com.br"],
        assunto="Folha de pagamento",
        corpo="Segue a folha de salários.",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "sem_loja" and m.conversa_id is None
    assert await contar(db, AtendimentoConversa) == 0


async def test_aviso_do_ml_para_o_sac_que_e_login_da_loja_vai_para_a_loja(db, cena):
    # sac@uranyx.com.br é a caixa de site E o login da loja do ML (cadastro "sac@uranyx").
    vr = await _integ(db, cena.dono, IntegrationPlatform.ML, "VR")
    await _loja(db, cena.dono, "mercadolivre", "sac@uranyx", vr, "VR")
    await db.commit()
    for pasta in ("INBOX", "*uranyx sac"):
        mid = await entrar(
            db,
            cena.geral,
            pasta=pasta,
            de="noreply@mercadolivre.com",
            de_nome="Mercado Livre",
            para=["sac@uranyx.com.br"],
            assunto="Nova reclamação na venda 2000012345678901",
            corpo="Você tem uma nova reclamação.",
        )
        await rodar(db)
        m = await meta_de(db, mid)
        assert m.integration_id == vr.id and m.plataforma == "ml", pasta
        assert m.protocolo is None and not any(a["codigo"] == "sem_protocolo" for a in m.alertas)


async def test_chamado_agrupado_no_mais_novo_nao_reabre_o_fechado(db, cena):
    from app.services.mail_atendimento import chamados

    ids = []
    for protocolo in ("US-26-0001", "US-26-0002"):
        ids.append(
            await entrar(
                db,
                cena.geral,
                pasta="*uranyx sac",
                de="sac@uranyx.com.br",
                para=["sac@uranyx.com.br"],
                assunto=f"[{protocolo}] Dúvida",
                corpo="E-mail: maria@gmail.com\nMensagem: oi",
            )
        )
        await rodar(db)
    velho, novo = await meta_de(db, ids[0]), await meta_de(db, ids[1])
    origem = await conversa(db, velho.conversa_id)
    destino = await conversa(db, novo.conversa_id)
    # O MAIS VELHO agrupado no MAIS NOVO (o caso que o teste de antes não cobria).
    await chamados.agrupar(db, origem, destino, cena.dono)
    await db.commit()
    # A cliente escreve de novo com o protocolo do velho, sem References.
    volta = await entrar(
        db,
        cena.geral,
        pasta="*uranyx sac",
        de="sac@uranyx.com.br",
        para=["sac@uranyx.com.br"],
        assunto="Re: [US-26-0001] Dúvida",
        corpo="E-mail: maria@gmail.com\nMensagem: e aí?",
    )
    await rodar(db)
    m = await meta_de(db, volta)
    assert m.conversa_id == destino.id and m.vinculado_por == "protocolo"
    assert (await conversa(db, origem.id)).situacao == "fechada"


# ─────────────── idempotência, erro e a volta da resposta ───────────────


async def test_a_ponte_nunca_grava_duas_mensagens_do_mesmo_email(db, cena):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    await rodar(db)
    # Outra volta que chegasse junto: a reserva da meta já existe.
    assert await ponte.processar(db, mid) is None
    assert await contar(db, AtendimentoMensagem) == 1
    assert await contar(db, MailMessageMeta) == 1


async def test_um_email_que_falha_vira_erro_e_nao_para_os_outros(db, cena, monkeypatch):
    ruim = await entrar(db, cena.geral, assunto="ruim")
    bom = await entrar(db, cena.geral, assunto="bom")
    original = ponte.decifrar

    def _decifrar(message):
        if message.id == ruim:
            raise RuntimeError("quebrou")
        return original(message)

    monkeypatch.setattr(ponte, "decifrar", _decifrar)
    resumo = await rodar(db)
    assert resumo["erros"] == 1 and resumo["processados"] == 1
    m = await meta_de(db, ruim)
    assert (m.estado, m.motivo) == ("erro", "RuntimeError")
    assert (await meta_de(db, bom)).estado == "sem_vinculo"
    # Reprocessar (por pessoa) leva de novo pela ponte.
    monkeypatch.setattr(ponte, "decifrar", original)
    assert await ponte.reprocessar(db, ruim) == "sem_vinculo"
    await db.commit()
    assert (await meta_de(db, ruim)).conversa_id is not None


async def _job(
    db, cena, message_id: UUID, mensagem: AtendimentoMensagem, status: str
) -> MailOutbox:
    job = MailOutbox(
        id=uuid4(),
        mailbox_id=cena.geral.id,
        message_id=message_id,
        author_user_id=cena.dono.id,
        request_id=mensagem.id,
        content_enc=encrypt_json({"text": "x", "to": "maria@gmail.com", "from_address": "a@b.c"}),
        status=status,
    )
    db.add(job)
    await db.flush()
    db.add(
        MailOutboxMeta(
            outbox_id=job.id,
            atendimento_mensagem_id=mensagem.id,
            conversa_id=mensagem.conversa_id,
            origem="conversa",
            status_visto="queued",
        )
    )
    await db.commit()
    return job


@pytest.mark.parametrize(
    ("status", "esperado", "erro"),
    [("sent", "enviada", None), ("failed", "falhou", "mail:x"), ("uncertain", "revisar", "mail:")],
)
async def test_o_status_do_job_volta_para_a_mensagem(db, cena, status, esperado, erro):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    m = await meta_de(db, mid)
    nossa = AtendimentoMensagem(
        conversa_id=m.conversa_id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto="Chega amanhã.",
        status="enviando",
        payload={},
    )
    db.add(nossa)
    await db.commit()
    job = await _job(db, cena, mid, nossa, "queued")
    job.status = status
    job.error_code = "x" if status == "failed" else None
    job.completed_at = datetime.now(UTC)
    if status == "sent":
        job.receipt_enc = encrypt_json(
            {"status": "sent", "message_id": "<real@tuta.com>", "error_code": None}
        )
    await db.commit()
    assert (await rodar(db))["envios"] == 1
    await db.refresh(nossa)
    assert nossa.status == esperado
    if erro:
        assert nossa.erro.startswith(erro)
    liga = await db.get(MailOutboxMeta, job.id)
    await db.refresh(liga)
    assert liga.status_visto == status
    if status == "sent":
        assert liga.enviado_mid_hash == ponte.hash_mid("<real@tuta.com>")
        c = await conversa(db, m.conversa_id)
        assert c.aguardando_resposta is False
    # A volta seguinte não mexe de novo.
    assert (await rodar(db))["envios"] == 0


async def test_a_resposta_do_cliente_a_nossa_resposta_acha_a_conversa(db, cena):
    mid = await entrar(db, cena.geral, para=["16tr@tuta.com"])
    await rodar(db)
    # A JLAS2 (ficha sem integração) tem a conversa dela; não atrapalha a de baixo.
    assert (await meta_de(db, mid)).estado == "sem_vinculo"
    mid = await entrar(db, cena.geral)
    await rodar(db)
    m = await meta_de(db, mid)
    nossa = AtendimentoMensagem(
        conversa_id=m.conversa_id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto="ok",
        status="enviando",
        payload={},
    )
    db.add(nossa)
    await db.commit()
    job = await _job(db, cena, mid, nossa, "queued")
    job.status = "sent"
    job.completed_at = datetime.now(UTC)
    job.receipt_enc = encrypt_json({"status": "sent", "message_id": "<nossa@tuta.com>"})
    await db.commit()
    await rodar(db)
    # O cliente responde à NOSSA resposta, pela Entrada (sem plataforma pela pasta).
    resposta = await entrar(
        db, cena.geral, pasta="INBOX", in_reply_to="<nossa@tuta.com>", corpo="Obrigada!"
    )
    await rodar(db)
    m2 = await meta_de(db, resposta)
    assert m2.conversa_id == m.conversa_id and m2.vinculado_por == "referencia"


async def test_o_nosso_envio_voltando_nos_enviados_nao_duplica(db, cena):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    m = await meta_de(db, mid)
    conversa_id = m.conversa_id
    nossa = AtendimentoMensagem(
        conversa_id=conversa_id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto="Chega amanhã.",
        status="enviando",
        payload={},
    )
    db.add(nossa)
    await db.commit()
    job = await _job(db, cena, mid, nossa, "queued")
    # O Enviados foi lido ANTES do recibo: espera a próxima volta.
    enviado = await entrar(
        db,
        cena.geral,
        pasta="Sent",
        de="21max@tuta.com",
        para=["maria@gmail.com"],
        direcao="sent",
        in_reply_to=(await _mid_original(db, mid)),
        corpo="Chega amanhã.\n\nAtenciosamente,\nBarbosa",
        recebido_em=datetime.now(UTC),
    )
    assert (await rodar(db))["esperando"] == 1
    assert await meta_de(db, enviado) is None
    job.status = "sent"
    job.completed_at = datetime.now(UTC)
    job.receipt_enc = encrypt_json({"status": "sent", "message_id": None})
    await db.commit()
    await rodar(db)
    e = await meta_de(db, enviado)
    assert (e.estado, e.vinculado_por, e.mensagem_id) == ("gravado", "nossa_resposta", nossa.id)
    assert len(await mensagens(db, conversa_id)) == 2  # o e-mail + a nossa resposta, só


async def _mid_original(db, message_id: UUID) -> str:
    from app.security.cipher import decrypt_json

    return decrypt_json((await db.get(MailMessage, message_id)).content_enc)["message_id"]


async def test_resposta_dada_direto_no_tuta_entra_como_externa(db, cena):
    mid = await entrar(db, cena.geral)
    await rodar(db)
    m = await meta_de(db, mid)
    enviado = await entrar(
        db,
        cena.geral,
        pasta="Sent",
        de="21max@tuta.com",
        para=["maria@gmail.com"],
        direcao="sent",
        in_reply_to=await _mid_original(db, mid),
        corpo="Respondi pelo Tuta.",
    )
    await rodar(db)
    e = await meta_de(db, enviado)
    assert e.estado == "gravado" and e.conversa_id == m.conversa_id
    externa = await db.get(AtendimentoMensagem, e.mensagem_id)
    assert (externa.autor, externa.origem, externa.status) == ("loja", "externo", "enviada")
    assert externa.payload["mail"]["fora_do_davinci"] is True


async def test_anexo_fica_so_na_central(db, cena):
    mid = await entrar(db, cena.geral, anexos=[("nota.pdf", "application/pdf", b"%PDF-1")])
    await rodar(db)
    m = await meta_de(db, mid)
    msg = await db.get(AtendimentoMensagem, m.mensagem_id)
    assert msg.anexos == [] and msg.payload["mail"]["anexos"] == 1


async def test_vez_da_loja_no_aviso_de_plataforma_sem_api(db, cena):
    temu = await _integ(db, cena.dono, IntegrationPlatform.TEMU, "jonas-temu")
    await _loja(db, cena.dono, "temu", "jonas13", temu, "Jonas")
    cena.geral.config_enc = encrypt_json(
        {"address": GERAL, "aliases": [GERAL, *ALIASES_GERAL, "jonas13@tuta.com"]}
    )
    await db.commit()
    mid = await entrar(
        db,
        cena.geral,
        pasta="problema temu",
        para=["jonas13@tuta.com"],
        de="noreply@temu.com",
        de_nome="Temu",
        assunto="Disputa aberta",
    )
    await rodar(db)
    c = await conversa(db, (await meta_de(db, mid)).conversa_id)
    assert c.aguardando_resposta is True and CHAVE_VEZ_DA_LOJA in c.dados


# ─────── conferência pré-subida de 08/10 (os casos do cético) ───────

# Para 21max (LOGIN das lojas Barbosa), pasta "mensagens ml": com frase fora
# da lista, o código ou a senha viravam conversa (sem_vinculo) e a equipe lia.
LOGIN_DA_LOJA = [
    ("contato@kwai.com", "Acesso à conta", "Use o código 482913 para entrar na sua conta."),
    ("no-reply@mercadolivre.com.br", "Token", "Seu token de acesso: 482913"),
    ("suporte@fornecedor.com.br", "Dados de acesso", "Usuário: barbosa\nSenha gerada: Kx81mq2z"),
    (
        "security@facebookmail.com",
        "482913 is your Facebook confirmation code",
        "Enter this code: 482913",
    ),
    ("account@service.com", "Sign in", "Your sign-in code is 482913"),
    ("cuenta@tienda.com", "Código de confirmación", "El código de confirmación es 482913"),
    ("cuenta@tienda.com", "Tu cuenta", "Tu contraseña temporal es: Kx81mq2z"),
]


@pytest.mark.parametrize(("de", "assunto", "corpo"), LOGIN_DA_LOJA)
async def test_codigo_ou_senha_sem_frase_da_lista_nunca_vira_conversa(db, cena, de, assunto, corpo):
    mid = await entrar(db, cena.geral, pasta="mensagens ml", de=de, assunto=assunto, corpo=corpo)
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo) == ("seguranca", "codigo_ou_acesso")
    assert m.conversa_id is None and m.mid_hash is None and m.alias_recebido is None
    assert await contar(db, AtendimentoMensagem) == 0
    assert await contar(db, AtendimentoConversa) == 0


async def test_codigo_sem_frase_no_alias_de_loja_da_goslin(db, cena):
    mid = await entrar(
        db,
        cena.goslin,
        pasta="INBOX",
        de="contato@kwai.com",
        para=["mia30@tuta.com"],
        assunto="Acesso",
        corpo="Use o código 482913 para entrar na sua conta.",
    )
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "seguranca"
    assert await contar(db, AtendimentoConversa) == 0


# atacado@ e duvidas@uranyx.com.br: caixas de SITE (marca_emails) que podem ser
# o login do painel do site, do Meta, do Google. O aviso de acesso de um
# SERVIÇO ali não afrouxa (virava chamado com a senha em claro).
CAIXA_DE_SITE = [
    (
        "seguranca@nuvemshop.com.br",
        "Painel",
        "Sua senha provisória Kx81mq2z",
        "atacado@uranyx.com.br",
    ),
    (
        "security@facebookmail.com",
        "Novo login",
        "Detectamos um novo login na sua conta.",
        "duvidas@uranyx.com.br",
    ),
    (
        "seguranca@nuvemshop.com.br",
        "Redefinição de senha",
        "Clique para redefinir sua senha: https://uranyx.lojavirtualnuvem.com.br/r/AbCd",
        "atacado@uranyx.com.br",
    ),
    ("account@service.com", "Sign in", "Your sign-in code is 482913", "duvidas@uranyx.com.br"),
    (
        "noreply@google.com",
        "Alerta de segurança",
        "Nova senha definida na sua conta.",
        "sac@uranyx.com.br",
    ),
]


@pytest.mark.parametrize(("de", "assunto", "corpo", "para"), CAIXA_DE_SITE)
async def test_aviso_de_acesso_de_servico_na_caixa_de_site_e_seguranca(
    db, cena, de, assunto, corpo, para
):
    mid = await entrar(
        db, cena.geral, pasta="INBOX", de=de, para=[para], assunto=assunto, corpo=corpo
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "seguranca", m.estado
    assert await contar(db, AtendimentoConversa) == 0


async def test_cliente_de_verdade_na_caixa_de_site_continua_chamado(db, cena):
    # A pessoa (gmail) escrevendo para atacado@ sobre a senha: é atendimento (chamado).
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="joao.revenda@gmail.com",
        para=["atacado@uranyx.com.br"],
        assunto="Esqueci minha senha",
        corpo="Esqueci minha senha do site de atacado, como faço?",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado" and m.conversa_id is not None
    assert (await conversa(db, m.conversa_id)).plataforma == "site"


async def test_aviso_normal_da_plataforma_nao_vira_seguranca_nem_bolinha(db, cena):
    # Controle: pedido, rastreio, CEP, valor e data no aviso do ML (para o login da loja).
    corpo = (
        "Seu pedido 2000012345678901 foi enviado. Código de rastreio: AA123456789BR. "
        "Entrega até 10/10/2026 no CEP 01310-100. Total R$ 1.299,00."
    )
    mid = await entrar(
        db,
        cena.geral,
        pasta="mensagens ml",
        de="no-reply@mercadolivre.com.br",
        assunto="Pedido enviado",
        corpo=corpo,
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado != "seguranca" and m.conversa_id is not None
    assert (await db.get(AtendimentoMensagem, m.mensagem_id)).texto == corpo


async def _conversa_do_gmail(db, cena) -> UUID:
    """A conversa da Amazon como o leitor do Gmail (`amazon_email`) grava: canal
    email, SEM `dados.fonte` — fora da ponte. (O id: a volta que espera desfaz
    a transação e expira os objetos da sessão.)"""
    conv = AtendimentoConversa(
        integration_id=cena.amazon.id,
        plataforma="amazon",
        canal="email",
        externo_id="amz-gmail-thread-1",
        conta="Mike",
        pedido_marketplace="701-1234567-1234567",
        dados={},
    )
    db.add(conv)
    await db.flush()
    db.add(
        AtendimentoMensagem(
            conversa_id=conv.id,
            autor="cliente",
            origem="cliente",
            externo_id="<mid1@marketplace.amazon.com.br>",
            texto="Oi, quando chega?",
            enviada_em=AGORA - timedelta(hours=1),
        )
    )
    await db.commit()
    return conv.id


async def _amazon(db, cena, n: int, *, criado_em: datetime | None = None) -> UUID:
    mid = await entrar(
        db,
        cena.geral,
        pasta="mensagens amazon",
        de="abc123@marketplace.amazon.com.br",
        para=["mike14@tuta.com"],
        message_id=f"<mid{n}@marketplace.amazon.com.br>",
        in_reply_to="<mid1@marketplace.amazon.com.br>" if n > 1 else None,
        assunto="Pedido 701-1234567-1234567",
        corpo="Ainda não chegou." if n > 1 else "Oi, quando chega?",
        tuta={"conversation_id": "FIO-AMZ-1"},
    )
    if criado_em is not None:
        message = await db.get(MailMessage, mid)
        message.created_at = criado_em
        await db.commit()
    return mid


async def _so_mensagens_do_gmail(db, conv: UUID) -> None:
    msgs = await mensagens(db, conv)
    assert not any(isinstance(m.payload, dict) and "mail" in m.payload for m in msgs)
    assert (await conversa(db, conv)).dados.get("fonte") != "tuta"


async def test_amazon_fio_da_conversa_do_gmail_espera_o_gmail(db, cena):
    """O 2º e-mail do fio chega no Tuta ANTES do Gmail: espera (nada gravado) e,
    quando o Gmail o lê, só liga — nunca uma cópia da ponte na conversa do Gmail."""
    conv = await _conversa_do_gmail(db, cena)
    m1 = await _amazon(db, cena, 1)
    await rodar(db)
    assert (await meta_de(db, m1)).vinculado_por == "amazon_gmail"
    m2 = await _amazon(db, cena, 2)
    resumo = await rodar(db)
    assert resumo["esperando"] == 1 and await meta_de(db, m2) is None
    await _so_mensagens_do_gmail(db, conv)
    # O Gmail leu a mensagem: a volta seguinte liga.
    db.add(
        AtendimentoMensagem(
            conversa_id=conv,
            autor="cliente",
            origem="cliente",
            externo_id="<mid2@marketplace.amazon.com.br>",
            texto="Ainda não chegou.",
            enviada_em=AGORA,
        )
    )
    await db.commit()
    await rodar(db)
    meta2 = await meta_de(db, m2)
    assert (meta2.estado, meta2.vinculado_por, meta2.conversa_id) == (
        "gravado",
        "amazon_gmail",
        conv,
    )
    assert len(await mensagens(db, conv)) == 2
    await _so_mensagens_do_gmail(db, conv)


async def test_amazon_sem_o_gmail_depois_da_espera_vai_para_conversa_a_parte(db, cena):
    conv = await _conversa_do_gmail(db, cena)
    await _amazon(db, cena, 1)
    await rodar(db)
    m2 = await _amazon(db, cena, 2, criado_em=AGORA - timedelta(hours=7))
    await rodar(db)
    meta2 = await meta_de(db, m2)
    assert meta2.conversa_id is not None and meta2.conversa_id != conv
    assert any(a["codigo"] == "amazon_sem_gmail" for a in meta2.alertas)
    assert ponte.e_conversa_da_ponte(await conversa(db, meta2.conversa_id))
    await _so_mensagens_do_gmail(db, conv)


async def test_tela_so_trata_como_email_da_ponte_a_conversa_da_ponte(db, cena, client, auth_as):
    conv = await _conversa_do_gmail(db, cena)
    mid = await entrar(db, cena.geral)
    await rodar(db)
    da_ponte = (await meta_de(db, mid)).conversa_id
    auth_as(cena.dono)
    r = await client.get(f"/api/atendimento/conversas/{da_ponte}")
    assert r.status_code == 200, r.text
    assert r.json()["conversa"]["email_da_ponte"] is True
    r = await client.get(f"/api/atendimento/conversas/{conv}")
    assert r.status_code == 200, r.text
    assert r.json()["conversa"]["email_da_ponte"] is False


# ─────── conferência pré-subida 2 de 08/10 (o cético 2: e-mails novos) ───────

# Para 21max (LOGIN das lojas Barbosa), pasta "mensagens ml": o código único
# sem palavra da lista, a senha escrita em frase e o link com prazo viravam
# conversa (sem_vinculo) e quem só lê via o segredo. (de, assunto, corpo, segredo)
CETICO_2_LOGIN_DA_LOJA = [
    (
        "noreply@mercadolivre.com",
        "Confirme que é você",
        "Olá, Barbosa!\n\nPara continuar, digite este número no Mercado Livre:\n\n731 604\n\n"
        "Ele vence em 30 minutos. Não compartilhe com ninguém.",
        "731 604",
    ),
    (
        "comunicado@itau.com.br",
        "Itaú: habilitação de aparelho",
        "Para habilitar o iToken no seu novo aparelho, informe a chave 804 117 no app Itaú.",
        "804 117",
    ),
    (
        "seguranca@c6bank.com.br",
        "Liberação do novo celular",
        "Sua chave de segurança para liberar o novo celular é 7 3 1 8 2 0.\n\n"
        "Não informe a ninguém.",
        "7 3 1 8 2 0",
    ),
    (
        "todomundo@nubank.com.br",
        "Confirme sua identidade",
        "Para concluir, digite o número 615 029 no app.",
        "615 029",
    ),
    (
        "alerts@wise.com",
        "Confirm it's you",
        "Enter 604 381 on the Wise website to approve this request. It expires in 10 minutes.",
        "604 381",
    ),
    (
        "notificaciones@banco.com.ar",
        "Confirmá tu identidad",
        "Tu número de verificación es 903 512. Vence en 5 minutos.",
        "903 512",
    ),
    (
        "suporte@sistemaxpto.com.br",
        "Senha redefinida",
        "Olá, sua senha foi redefinida para Xp7!kL2wQz. Recomendamos trocá-la no próximo acesso.",
        "Xp7!kL2wQz",
    ),
    (
        "support@labelhub.co",
        "Your LabelHub account",
        "Your password has been reset to Zq!8mw#Lp4. Please sign in and change it.",
        "Zq!8mw#Lp4",
    ),
    (
        "carlos.mendes@distribuidoraalfa.com.br",
        "Acesso ao B2B",
        "Bom dia Eduardo,\n\nLiberei o acesso de vocês ao nosso portal de pedidos. O login é o "
        "CNPJ e a senha ficou Alfa@2026uranyx (pode trocar depois).\n\nAbraço, Carlos",
        "Alfa@2026uranyx",
    ),
    (
        "rafael@agenciapixel.com.br",
        "Painel da loja",
        "Fala pessoal, troquei a senha do painel da Nuvemshop, agora é Ur4nyx!Painel. "
        "Me avisa se der certo.",
        "Ur4nyx!Painel",
    ),
    (
        "soporte@proveedormx.com",
        "Tu acceso",
        "Hola, tu usuario es uranyx y tu contraseña quedó como Prov#2026mx. Cualquier cosa me "
        "avisas.",
        "Prov#2026mx",
    ),
    (
        "joao.socio@gmail.com",
        "senha nova do ML",
        "Troquei a do ML pra MlBarb0sa#26, anota aí",
        "MlBarb0sa#26",
    ),
    (
        "team@getsellerapp.io",
        "Welcome back to SellerApp",
        "Click here to log into your dashboard: https://getsellerapp.io/m/7QpX2v "
        "(expires in 15 minutes)",
        "7QpX2v",
    ),
    (
        "hola@tiendita.mx",
        "Tu enlace de acceso",
        "Accede a tu cuenta con este enlace: https://tiendita.mx/e/Qm3x9\n\nCaduca en 15 minutos.",
        "Qm3x9",
    ),
    # As variantes da senha em frase.
    (
        "suporte@fornecedorx.com.br",
        "Account",
        "Your password was reset to Xp7!kL2wQz",
        "Xp7!kL2wQz",
    ),
    (
        "suporte@fornecedorx.com.br",
        "Senha",
        "Sua senha foi alterada para Xp7!kL2wQz.",
        "Xp7!kL2wQz",
    ),
    ("suporte@fornecedorx.com.br", "Acesso", "Senha (temporária): Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("suporte@fornecedorx.com.br", "Acesso", "Senha → Xp7!kL2wQz", "Xp7!kL2wQz"),
    ("soporte@proveedormx.com", "Acceso", "Tu nueva contraseña será Xp7!kL2wQz", "Xp7!kL2wQz"),
]


def _do_mac(assunto: str, corpo: str) -> tuple[str, str]:
    """O que o conector do Mac sobe (a mesma máscara do Python: o teste de paridade)."""
    from app.services.mail_atendimento import codigos

    p = codigos.proteger(assunto, corpo)
    return p.assunto, p.texto


@pytest.mark.parametrize("pelo_mac", [False, True], ids=["cru", "mascarado_no_mac"])
@pytest.mark.parametrize(("de", "assunto", "corpo", "segredo"), CETICO_2_LOGIN_DA_LOJA)
async def test_cetico_2_no_login_da_loja_nunca_vira_conversa(
    db, cena, de, assunto, corpo, segredo, pelo_mac
):
    if pelo_mac:
        assunto, corpo = _do_mac(assunto, corpo)
        assert segredo not in assunto + corpo
    mid = await entrar(db, cena.geral, pasta="mensagens ml", de=de, assunto=assunto, corpo=corpo)
    await rodar(db)
    m = await meta_de(db, mid)
    assert (m.estado, m.motivo) == ("seguranca", "codigo_ou_acesso")
    assert m.conversa_id is None and m.mid_hash is None and m.alias_recebido is None
    assert await contar(db, AtendimentoMensagem) == 0
    assert await contar(db, AtendimentoConversa) == 0


async def test_cetico_2_na_goslin_o_codigo_unico_nunca_vira_conversa(db, cena):
    mid = await entrar(
        db,
        cena.goslin,
        pasta="INBOX",
        de="noreply@mercadolivre.com",
        para=["mia30@tuta.com"],
        assunto="Confirme que é você",
        corpo="Para continuar, digite este número no Mercado Livre: 731 604. Vence em 30 minutos.",
    )
    await rodar(db)
    assert (await meta_de(db, mid)).estado == "seguranca"
    assert await contar(db, AtendimentoConversa) == 0


# A caixa de SITE (atacado@) com remetente de "cara de pessoa": o código do
# serviço (E06) é de segurança; o resto vira chamado com o segredo mascarado
# — nem a conversa nem quem só lê vê o segredo. (de, assunto, corpo, segredo, estado)
CETICO_2_CAIXA_DE_SITE = [
    (
        "feedback@slack.com",
        "Slack confirmation code: QXF-7KT",
        "Your confirmation code is below — enter it in your open browser window and we'll help "
        "you get signed in.\n\nQXF-7KT",
        "QXF-7KT",
        "seguranca",
    ),
    (
        "atendimento@portalfornecedor.com.br",
        "Cadastro aprovado",
        "Sua senha de acesso foi gerada: Kx81mq2z",
        "Kx81mq2z",
        "gravado",
    ),
    (
        "joao.socio@gmail.com",
        "senha nova do ML",
        "Troquei a do ML pra MlBarb0sa#26, anota aí",
        "MlBarb0sa#26",
        "gravado",
    ),
    (
        "carlos.mendes@distribuidoraalfa.com.br",
        "Acesso ao B2B",
        "O login é o CNPJ e a senha ficou Alfa@2026uranyx (pode trocar depois).",
        "Alfa@2026uranyx",
        "gravado",
    ),
    (
        "contato@bancointer.com.br",
        "Confirme que é você",
        "Para continuar, digite este número no app: 731 604. Ele vence em 30 minutos.",
        "731 604",
        "gravado",
    ),
    (
        "ola@loggi.com",
        "Seu acesso à Loggi",
        "Toque para abrir o app já logado: abrir <https://lg.gy/a/Zp81Kd>",
        "Zp81Kd",
        "gravado",
    ),
    # As variantes da senha em frase, de remetente com "cara de pessoa": mascaradas.
    (
        "suporte@fornecedorx.com.br",
        "Senha",
        "Sua senha foi alterada para Xp7!kL2wQz.",
        "Xp7!kL2wQz",
        "gravado",
    ),
    (
        "suporte@fornecedorx.com.br",
        "Acesso",
        "Senha (temporária): Xp7!kL2wQz",
        "Xp7!kL2wQz",
        "gravado",
    ),
    ("suporte@fornecedorx.com.br", "Acesso", "Senha → Xp7!kL2wQz", "Xp7!kL2wQz", "gravado"),
    (
        "suporte@fornecedorx.com.br",
        "Acceso",
        "Tu nueva contraseña será Xp7!kL2wQz",
        "Xp7!kL2wQz",
        "gravado",
    ),
    (
        "suporte@fornecedorx.com.br",
        "Account",
        "Your password was reset to Xp7!kL2wQz",
        "Xp7!kL2wQz",
        "gravado",
    ),
]


@pytest.mark.parametrize(("de", "assunto", "corpo", "segredo", "estado"), CETICO_2_CAIXA_DE_SITE)
async def test_cetico_2_caixa_de_site_nunca_mostra_o_segredo(
    db, cena, client, make_user, auth_as, monkeypatch, de, assunto, corpo, segredo, estado
):
    import json

    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de=de,
        para=["atacado@uranyx.com.br"],
        assunto=assunto,
        corpo=corpo,
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == estado, m.estado
    sem_simbolo = segredo.replace(" ", "").replace("-", "")
    if m.conversa_id is None:
        assert await contar(db, AtendimentoConversa) == 0
        return
    msg = await db.get(AtendimentoMensagem, m.mensagem_id)
    conv = await conversa(db, m.conversa_id)
    vistos = [msg.texto, json.dumps(msg.payload, ensure_ascii=False), json.dumps(conv.dados)]
    # Quem só LÊ (sem equipe) pelas rotas da conversa e dos cartões.
    monkeypatch.setattr(get_settings(), "atendimento_usuarios", "quem-mexe@davinci-test.com")
    leitor = await make_user(role=UserRole.USER)
    auth_as(leitor)
    for rota in (
        f"/api/atendimento/conversas/{m.conversa_id}",
        f"/api/atendimento/email/conversas/{m.conversa_id}/emails",
    ):
        r = await client.get(rota)
        assert r.status_code == 200, r.text
        vistos.append(r.text)
    for visto in vistos:
        assert segredo not in visto and sem_simbolo not in visto.replace(" ", "").replace("-", "")


async def test_cetico_2_link_magico_no_login_da_loja_sai_do_texto(db, cena):
    # X07 (sem prazo): vira conversa, mas o link (curto, sem cara de acesso) sai.
    mid = await entrar(
        db,
        cena.geral,
        pasta="mensagens ml",
        de="ola@loggi.com",
        assunto="Seu acesso à Loggi",
        corpo="Toque para abrir o app já logado: abrir <https://lg.gy/a/Zp81Kd>",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "sem_vinculo" and m.mensagem_id is not None
    texto_ = (await db.get(AtendimentoMensagem, m.mensagem_id)).texto
    assert "Zp81Kd" not in texto_ and "lg.gy" not in texto_
    assert texto_ == "Toque para abrir o app já logado: abrir <[link de acesso removido]>"


async def test_cetico_2_aviso_normal_da_plataforma_com_confirme_e_numero_continua(db, cena):
    # Controle: "confirme", "vence", "informe" e número de pedido/devolução no aviso do ML.
    corpo = (
        "Pedido 2000005555666677\nMensagem do comprador: não consigo acessar o rastreio, o "
        "número 48291375 não funciona. Confirme o envio até 12/10, a devolução 48291375 vence "
        "em 2 dias."
    )
    mid = await entrar(
        db,
        cena.geral,
        pasta="mensagens ml",
        de="noreply@mercadolivre.com",
        assunto="Você recebeu uma mensagem",
        corpo=corpo,
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado != "seguranca" and m.conversa_id is not None
    assert (await db.get(AtendimentoMensagem, m.mensagem_id)).texto == corpo


async def test_cetico_2_cliente_no_site_com_numero_de_serie_continua_inteiro(db, cena):
    corpo = (
        "Não consigo entrar no app Uranyx Care. Número de série K81Q2X, pedido 2000012345678901."
    )
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        de="renata.g@gmail.com",
        para=["sac@uranyx.com.br"],
        assunto="App Uranyx Care",
        corpo=corpo,
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado"
    assert (await db.get(AtendimentoMensagem, m.mensagem_id)).texto == corpo


# ─────── a resposta pela caixa crua da empresa (regressão da correção 6) ───────


async def test_resposta_pela_caixa_crua_da_empresa_aparece_na_conversa(db, cena):
    """Alguém responde pela caixa crua (E-mail › Caixas) da GERAL (empresa): o
    `queue_reply` cria a ligação `origem = caixa` (o freio por ela). A cópia no
    Enviados entra na conversa como a resposta dada fora do DaVinci — nunca é
    "adotada" como resposta da conversa (a conversa ficava aguardando e alguém
    respondia de novo)."""
    from app.models import MailMailbox
    from app.schemas.mail import ReplyIn
    from app.security.cipher import decrypt_json

    cfg = await db.get(MailMailboxSettings, cena.geral.id)
    cfg.envio_modo = "real"
    geral = await db.get(MailMailbox, cena.geral.id)
    geral.send_enabled = True
    geral.agent_can_send = True
    geral.state = "online"
    geral.last_seen_at = datetime.now(UTC)
    await db.commit()
    # O commit expira os objetos da sessão: relê (a ponte os lê sem await).
    await db.refresh(cfg)
    await db.refresh(geral)
    mid = await entrar(db, cena.geral)
    await rodar(db)
    m = await meta_de(db, mid)
    conversa_id = m.conversa_id
    assert conversa_id is not None, (m.estado, m.motivo)
    assert (await conversa(db, conversa_id)).aguardando_resposta
    message = await db.get(MailMessage, mid)
    job = await mail_central.queue_reply(
        db,
        await db.get(MailMailbox, cena.geral.id),
        message,
        cena.dono,
        ReplyIn(request_id=uuid4(), text="Respondi pela caixa crua", from_address="21max@tuta.com"),
    )
    await db.commit()
    job_id = job.id
    liga = await db.get(MailOutboxMeta, job_id)
    assert liga is not None and liga.origem == "caixa" and liga.caixa_empresa
    jobs = await mail_central.lease(db, await db.get(MailMailbox, cena.geral.id))
    await db.commit()
    assert [j["id"] for j in jobs] == [str(job_id)]
    j = await db.get(MailOutbox, job_id)
    j.status = "sent"
    j.completed_at = datetime.now(UTC)
    j.receipt_enc = encrypt_json({"status": "sent", "message_id": "<crua@tuta.com>"})
    await db.commit()
    await rodar(db)
    original = decrypt_json((await db.get(MailMessage, mid)).content_enc)["message_id"]
    enviado = await entrar(
        db,
        cena.geral,
        pasta="Sent",
        de="21max@tuta.com",
        para=["maria@gmail.com"],
        direcao="sent",
        in_reply_to=original,
        message_id="<crua@tuta.com>",
        corpo="Respondi pela caixa crua",
        recebido_em=datetime.now(UTC),
    )
    await rodar(db)
    e = await meta_de(db, enviado)
    assert (e.estado, e.vinculado_por, e.conversa_id) == ("gravado", "fio", conversa_id)
    msgs = await mensagens(db, conversa_id)
    assert len(msgs) == 2 and msgs[-1].payload["mail"]["fora_do_davinci"] is True
    assert (await conversa(db, conversa_id)).aguardando_resposta is False


# ─────── a Amazon que espera o Gmail não trava a fila (crítica pré-subida 2) ───────


async def _amazon_esperando(db, cena, n: int) -> UUID:
    return await entrar(
        db,
        cena.geral,
        pasta="mensagens amazon",
        de="abc123@marketplace.amazon.com.br",
        para=["mike14@tuta.com"],
        message_id=f"<mid{n}@marketplace.amazon.com.br>",
        in_reply_to="<mid1@marketplace.amazon.com.br>",
        assunto="Pedido 701-1234567-1234567",
        corpo=f"Mensagem {n}",
        tuta={"conversation_id": "FIO-AMZ-1"},
        recebido_em=AGORA - timedelta(minutes=30),
    )


async def test_amazon_esperando_o_gmail_nao_trava_o_lote_inteiro(db, cena):
    """50 da Amazon (o lote inteiro) esperam o Gmail e são os mais velhos: o
    e-mail comum de trás é lido NA MESMA volta."""
    await _conversa_do_gmail(db, cena)
    await _amazon(db, cena, 1)
    await rodar(db)
    for n in range(2, 52):
        await _amazon_esperando(db, cena, n)
    normal = await entrar(db, cena.geral, recebido_em=AGORA - timedelta(minutes=1))
    resumo = await rodar(db)
    assert resumo["esperando"] == 50 and resumo["processados"] == 1
    assert (await meta_de(db, normal)).estado in ("sem_vinculo", "gravado")


async def test_amazon_esperando_com_lote_pequeno_le_o_de_tras(db, cena):
    await _conversa_do_gmail(db, cena)
    await _amazon(db, cena, 1)
    await rodar(db)
    esperando = [await _amazon_esperando(db, cena, n) for n in (2, 3)]
    normal = await entrar(db, cena.geral, recebido_em=AGORA - timedelta(minutes=1))
    resumo = await ponte.rodar(db, limite=2)
    assert resumo["esperando"] == 2 and resumo["processados"] == 1
    assert await meta_de(db, normal) is not None
    assert all([await meta_de(db, x) is None for x in esperando])
    # A volta seguinte tenta de novo os que esperam (nada perdido).
    resumo = await ponte.rodar(db, limite=2)
    assert resumo["esperando"] == 2


async def test_amazon_depois_da_conversa_a_parte_o_proximo_ainda_espera_o_gmail(db, cena):
    """O 2º passou de 6 h sem o Gmail e foi para a conversa à parte. O 3º do
    MESMO fio também espera o Gmail (nunca entra direto na à parte): quando o
    Gmail o lê, ele fica numa conversa só."""
    conv = await _conversa_do_gmail(db, cena)
    await _amazon(db, cena, 1)
    await rodar(db)
    m2 = await _amazon(db, cena, 2, criado_em=AGORA - timedelta(hours=7))
    await rodar(db)
    a_parte = (await meta_de(db, m2)).conversa_id
    assert a_parte is not None and a_parte != conv
    m3 = await entrar(
        db,
        cena.geral,
        pasta="mensagens amazon",
        de="abc123@marketplace.amazon.com.br",
        para=["mike14@tuta.com"],
        message_id="<mid3@marketplace.amazon.com.br>",
        in_reply_to="<mid2@marketplace.amazon.com.br>",
        assunto="Pedido 701-1234567-1234567",
        corpo="Terceira mensagem.",
        tuta={"conversation_id": "FIO-AMZ-1"},
    )
    resumo = await rodar(db)
    assert resumo["esperando"] == 1 and await meta_de(db, m3) is None
    # O Gmail lê a 3ª: a volta seguinte só liga.
    db.add(
        AtendimentoMensagem(
            conversa_id=conv,
            autor="cliente",
            origem="cliente",
            externo_id="<mid3@marketplace.amazon.com.br>",
            texto="Terceira mensagem.",
            enviada_em=AGORA,
        )
    )
    await db.commit()
    await rodar(db)
    meta3 = await meta_de(db, m3)
    assert (meta3.vinculado_por, meta3.conversa_id) == ("amazon_gmail", conv)
    assert not any(x.texto == "Terceira mensagem." for x in await mensagens(db, a_parte))
