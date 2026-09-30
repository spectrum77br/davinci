# ruff: noqa: S105, S106  (senha de caixa falsa, nada real)
"""Adaptador da Amazon por e-mail: caixa só leitura, uma para as quatro contas.

O que estes testes seguram:

- o e-mail vira SÓ a mensagem do comprador (marcadores do modelo da Amazon,
  HTML, multipart, histórico citado cortado, anexo só pelo nome);
- só remetente de retransmissão da Amazon conta; o resto da caixa é ignorado;
- cada e-mail cai na conta certa: "+conta" do destinatário → pedido no
  espelho do Bling → thread anterior → "conta não identificada";
- a caixa abre em EXAMINE e o corpo vem por BODY.PEEK[] (nada vira lido);
- o cursor anda por UID, recomeça pelos 7 dias quando a UIDVALIDITY muda, e
  reler não duplica (Message-ID);
- a caixa é lida UMA vez por rodada, com o cursor igual em todos os canais
  Amazon e relido do banco dentro da trava;
- a resposta sai com os cabeçalhos da thread, sem "Re: Re:", com o pedido;
  recusa do SMTP é falha, queda depois do DATA é ambígua; o SMTP usa o
  usuário/senha próprios quando configurados;
- o e-mail seguinte SEM nº do pedido entra na conversa da thread (In-Reply-To,
  depois o endereço do comprador); a conversa "sem conta" é adotada quando a
  conta aparece;
- a devolução/recusa do que NÓS mandamos (mailer-daemon, aviso da Amazon)
  vira `falhou` e devolve a conversa à fila;
- o formato REAL (1º e-mail de 28/09/2026): marcadores "Mensagem:" /
  "Encerrar mensagem" (e "Iniciar mensagem" / "Mensagem final" na cópia),
  links do rodapé ("Solucionar o caso", caso) em `conversa.dados` e nunca no
  texto;
- a CÓPIA da resposta dada no Seller Central vira resposta da loja
  (`externo`) na conversa certa — conta pelo "+", pedido citado ou nome do
  comprador, pergunta anterior à cópia, até 7 dias —, tira da fila quando é
  a resposta mais nova, aposenta a sugestão da IA, é idempotente e nunca
  cria conversa nem chuta: empate de nome não fecha nenhuma (aviso
  "conferir"), cópia da NOSSA resposta só confirma a nossa, e a que chega
  antes da pergunta espera no cursor;
- os links do rodapé são só os do rodapé (o comprador não troca o botão);
- reler a caixa não grava de novo o e-mail que a thread mandaria para outra
  conversa.

Nada aqui abre rede: a caixa IMAP, o SMTP e o Redis são falsos. Os e-mails
"reais" são montados a partir das amostras mascaradas: nomes, textos, ids e
links são inventados (só o formato é o de verdade).
"""

from __future__ import annotations

import imaplib
import smtplib
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email import policy as politica_email
from email.message import EmailMessage
from email.utils import format_datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    BlingOrder,
    Company,
    Integration,
    IntegrationPlatform,
    Marketplace,
    Store,
    StoreInfo,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import amazon_email, gravar
from app.services.atendimento.amazon_email import SEM_CONTA

RELAY_MARIA = "abc123maria@marketplace.amazon.com.br"
RELAY_JOAO = "xyz789joao@marketplace.amazon.com.br"
RELAY_ANA = "qwe456ana@marketplace.amazon.com.br"
PEDIDO_KFA = "701-1111111-1111111"
PEDIDO_KIA = "702-2222222-2222222"
PEDIDO_SEM_DONO = "703-3333333-3333333"
CAIXA = "atendimento@loja-teste.com.br"


# ─────────────── falsos: e-mail, caixa IMAP, SMTP, Redis ───────────────


def _corpo(bruto: bytes) -> str:
    """Corpo de texto de um e-mail gerado (CRLF do SMTP vira \\n para comparar)."""
    msg = message_from_bytes(bruto, policy=politica_email.default)
    return msg.get_content().replace("\r\n", "\n").strip()


def _email(
    *,
    em_resposta_a: str | None = None,
    de: str = f'"Maria Silva - Amazon Marketplace" <{RELAY_MARIA}>',
    para: str = "atendimento+kfa@loja-teste.com.br",
    assunto: str = f"Pergunta sobre o pedido {PEDIDO_KFA}",
    data: str = "Thu, 24 Sep 2026 10:00:00 -0300",
    message_id: str | None = "<m1@amazon.com.br>",
    texto: str | None = "Olá, meu pedido ainda não chegou.",
    html: str | None = None,
    marcadores: bool = True,
    anexo: str | None = None,
) -> bytes:
    """Um e-mail como a Amazon manda (com o modelo em volta da mensagem)."""
    msg = EmailMessage()
    msg["From"] = de
    msg["To"] = para
    msg["Subject"] = assunto
    msg["Date"] = data
    if message_id:
        msg["Message-ID"] = message_id
    if em_resposta_a:
        msg["In-Reply-To"] = em_resposta_a
        msg["References"] = em_resposta_a
    if texto is not None:
        corpo = texto
        if marcadores:
            corpo = (
                "Você recebeu uma mensagem.\n\n"
                "------------- Mensagem: -------------\n\n"
                f"{texto}\n\n"
                "------------- Fim da mensagem -------------\n\n"
                f"Pedido nº {PEDIDO_KFA}. Para responder, responda a este e-mail.\n"
            )
        msg.set_content(corpo)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    elif html is not None:
        msg.set_content(html, subtype="html")
    if anexo:
        msg.add_attachment(b"%PDF-1.4 falso", maintype="application", subtype="pdf",
                           filename=anexo)
    return msg.as_bytes()


class CaixaFalsa:
    """A caixa do provedor: guarda os e-mails e registra cada comando.

    O `ImapFalso` só tem os métodos que o adaptador pode usar — um STORE,
    COPY ou EXPUNGE daria AttributeError (e o comando nem existe aqui).
    """

    def __init__(self, emails: dict[int, bytes] | None = None, uidvalidity: int = 777):
        self.emails = dict(emails or {})
        self.uidvalidity = uidvalidity
        self.log: list[tuple] = []
        self.conexoes = 0
        self.erro_login: str | None = None

    def abrir(self, config):
        self.conexoes += 1
        return ImapFalso(self)

    def comandos(self, nome: str) -> list[tuple]:
        return [c for c in self.log if c[0] == nome]


class ImapFalso:
    def __init__(self, caixa: CaixaFalsa):
        self.caixa = caixa

    def login(self, usuario, senha):
        self.caixa.log.append(("login", usuario))
        if self.caixa.erro_login:
            raise imaplib.IMAP4.error(self.caixa.erro_login)
        return "OK", [b"Logged in"]

    def select(self, pasta, readonly=False):
        self.caixa.log.append(("select", pasta, readonly))
        return "OK", [str(len(self.caixa.emails)).encode()]

    def response(self, code):
        if code == "UIDVALIDITY":
            return code, [str(self.caixa.uidvalidity).encode()]
        return code, [None]

    def uid(self, comando, *args):
        self.caixa.log.append(("uid", comando, *args))
        uids = sorted(self.caixa.emails)
        if comando == "SEARCH" and args[0] == "SINCE":
            return "OK", [" ".join(map(str, uids)).encode()]
        if comando == "SEARCH" and args[0] == "UID":
            inicio = int(args[1].split(":")[0])
            achados = [u for u in uids if u >= inicio]
            if not achados and uids:
                achados = [uids[-1]]  # regra do IMAP: `N:*` sempre inclui o maior UID
            return "OK", [" ".join(map(str, achados)).encode()]
        if comando == "FETCH":
            assert args[1] == "(BODY.PEEK[])", "corpo só por BODY.PEEK (não marca lido)"
            itens: list = []
            for i, u in enumerate(int(x) for x in args[0].split(",")):
                bruto = self.caixa.emails.get(u)
                if bruto is None:
                    continue
                itens.append((f"{i + 1} (UID {u} BODY[] {{{len(bruto)}}}".encode(), bruto))
                itens.append(b")")
            return "OK", itens
        raise AssertionError(f"comando IMAP inesperado: {comando} {args}")

    def logout(self):
        self.caixa.log.append(("logout",))
        return "BYE", [b"bye"]


class RedisFalso:
    def __init__(self):
        self.dados: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        self.ttl[chave] = ex
        return True

    async def get(self, chave):
        return self.dados.get(chave)

    async def delete(self, chave):
        self.dados.pop(chave, None)
        return 1

    def proxima_rodada(self):
        """A marca da rodada expirou: o próximo canal lê de novo."""
        self.dados.clear()


class SmtpFalso:
    def __init__(
        self,
        *,
        rcpt=(250, b"ok"),
        data=(250, b"2.0.0 queued"),
        erro_em: str | None = None,
        erro: BaseException | None = None,
        erro_no_quit: BaseException | None = None,
    ):
        self.rcpt_resposta = rcpt
        self.data_resposta = data
        self.erro_em = erro_em
        self.erro = erro
        self.erro_no_quit = erro_no_quit
        self.chamadas: list = []
        self.enviado: bytes | None = None

    def _talvez_falhe(self, fase):
        if self.erro_em == fase and self.erro is not None:
            raise self.erro

    def ehlo(self):
        self.chamadas.append("ehlo")
        return 250, b"ok"

    def starttls(self, context=None):
        self.chamadas.append("starttls")
        return 220, b"ready"

    def login(self, usuario, senha):
        self.chamadas.append(("login", usuario))
        self._talvez_falhe("login")
        return 235, b"ok"

    def mail(self, remetente):
        self.chamadas.append(("mail", remetente))
        return 250, b"ok"

    def rcpt(self, para):
        self.chamadas.append(("rcpt", para))
        return self.rcpt_resposta

    def data(self, conteudo):
        self.chamadas.append("data")
        self.enviado = conteudo
        self._talvez_falhe("data")
        return self.data_resposta

    def quit(self):
        self.chamadas.append("quit")
        if self.erro_no_quit is not None:
            raise self.erro_no_quit
        return 221, b"bye"

    def close(self):
        self.chamadas.append("close")


# ─────────────── fixtures ───────────────


@pytest.fixture
def caixa_configurada(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_amazon_imap_host", "imap.teste.local")
    monkeypatch.setattr(s, "atendimento_amazon_imap_usuario", CAIXA)
    monkeypatch.setattr(s, "atendimento_amazon_imap_senha", "senha-de-app")
    monkeypatch.setattr(s, "atendimento_amazon_imap_pasta", "INBOX")
    monkeypatch.setattr(s, "atendimento_amazon_smtp_host", "smtp.teste.local")
    monkeypatch.setattr(s, "atendimento_amazon_smtp_port", 587)
    monkeypatch.setattr(s, "atendimento_amazon_remetente", CAIXA)
    return s


@pytest.fixture
def caixa_desligada(monkeypatch):
    s = get_settings()
    for nome in (
        "atendimento_amazon_imap_host",
        "atendimento_amazon_imap_usuario",
        "atendimento_amazon_imap_senha",
        "atendimento_amazon_smtp_host",
        "atendimento_amazon_remetente",
    ):
        monkeypatch.setattr(s, nome, "")
    return s


@pytest.fixture
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(amazon_email, "redis", r)
    return r


def _instalar_caixa(monkeypatch, caixa: CaixaFalsa) -> CaixaFalsa:
    monkeypatch.setattr(amazon_email, "_abrir_imap", caixa.abrir)
    return caixa


async def _conta(
    db: AsyncSession,
    user: User,
    nome: str,
    *,
    platform: IntegrationPlatform = IntegrationPlatform.AMAZON,
    bling_loja_id: int | None = None,
    com_canal: bool = True,
) -> tuple[Integration, AtendimentoCanal | None]:
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json({"refresh_token": "rt"}),
        bling_loja_id=bling_loja_id,
    )
    db.add(integ)
    await db.flush()
    canal = None
    if com_canal and platform == IntegrationPlatform.AMAZON:
        canal = AtendimentoCanal(integration_id=integ.id, plataforma="amazon", canal="email")
        db.add(canal)
    await db.commit()
    return integ, canal


async def _pedido_no_bling(db: AsyncSession, numeroloja: str, loja: str) -> None:
    db.add(BlingOrder(numero="9001", numeroloja=numeroloja, loja=loja, item_index=0))
    await db.commit()


async def _conversas(db: AsyncSession) -> list[AtendimentoConversa]:
    return list(
        (await db.execute(select(AtendimentoConversa).order_by(AtendimentoConversa.externo_id)))
        .scalars()
        .all()
    )


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em)
            )
        )
        .scalars()
        .all()
    )


async def _cursores(db: AsyncSession) -> list[dict]:
    return list(
        (
            await db.execute(
                select(AtendimentoCanal.cursor).where(AtendimentoCanal.plataforma == "amazon")
            )
        )
        .scalars()
        .all()
    )


def _caixa_da_rodada_1() -> CaixaFalsa:
    """kfa pelo "+", kia pelo pedido, um sem dono, e dois que não são comprador."""
    return CaixaFalsa(
        {
            10: _email(),
            11: _email(
                de=f'"João Souza - Amazon Marketplace" <{RELAY_JOAO}>',
                para=CAIXA,
                assunto=f"Dúvida sobre o pedido {PEDIDO_KIA}",
                message_id="<m2@amazon.com.br>",
                texto="Vocês emitem nota fiscal?",
            ),
            12: _email(
                de=f"Ana <{RELAY_ANA}>",
                para=CAIXA,
                assunto=f"Pedido {PEDIDO_SEM_DONO}",
                message_id="<m3@amazon.com.br>",
                texto="Chegou quebrado.",
            ),
            13: _email(de="Fulano <fulano@gmail.com>", message_id="<spam@gmail.com>"),
            14: _email(
                de="Amazon <donotreply@marketplace.amazon.com.br>",
                message_id="<aviso@amazon.com.br>",
            ),
        }
    )


# ─────────────── texto do e-mail (funções puras) ───────────────


def test_texto_entre_os_marcadores_tag_pedido_e_nome():
    em = amazon_email.interpretar_email(
        _email(texto="Olá,\nmeu pedido ainda não chegou.\n\nPodem ver?"), uid=10
    )
    assert em is not None
    assert em.texto == "Olá,\nmeu pedido ainda não chegou.\n\nPodem ver?"  # sem o modelo
    assert em.remetente == RELAY_MARIA
    assert em.remetente_nome == "Maria Silva"  # sem o " - Amazon Marketplace"
    assert em.tag == "kfa"
    assert em.pedido == PEDIDO_KFA
    assert em.message_id == "<m1@amazon.com.br>"
    assert em.enviada_em == datetime(2026, 9, 24, 13, 0, tzinfo=UTC)  # -03:00 → UTC
    assert em.conversa_externo_id == f"{RELAY_MARIA}|{PEDIDO_KFA}"
    assert em.uid == 10
    assert em.anexos == []


def test_marcadores_em_ingles_e_pedido_so_no_corpo():
    corpo = (
        "------------- Message: -------------\n"
        "Where is my order?\n"
        "------------- End message -------------\n"
        f"Order ID: {PEDIDO_KIA}\n"
    )
    em = amazon_email.interpretar_email(
        _email(assunto="Question from buyer", texto=corpo, marcadores=False)
    )
    assert em.texto == "Where is my order?"
    assert em.pedido == PEDIDO_KIA  # não estava no assunto: veio do corpo


_RODAPE_AMAZON = (
    "For Your Information: To help arbitrate disputes and preserve trust and safety, "
    "we retain all messages buyers and sellers send through Amazon.com for two years."
)


@pytest.mark.parametrize(
    ("inicio", "fim"),
    [
        ("------------- Begin message -------------", "------------- End message -------------"),
        (
            "------------- Início da mensagem -------------",
            "------------- Fim da mensagem -------------",
        ),
        (
            "------------- Inicio da mensagem -------------",
            "------------- Fim da mensagem -------------",
        ),
    ],
)
def test_marcadores_do_modelo_real_begin_e_inicio_da_mensagem(inicio, fim):
    """API-7: só "Mensagem:"/"Message:" casava; com "Begin message" o modelo
    inteiro (cabeçalho do pedido, marcadores, rodapé) virava a mensagem."""
    corpo = (
        f"Order ID: {PEDIDO_KFA}\n"
        "# 1 Produto de teste\n\n"
        f"{inicio}\n\n"
        "Where is my order?\nIt is late.\n\n"
        f"{fim}\n\n"
        f"{_RODAPE_AMAZON}\n"
    )
    em = amazon_email.interpretar_email(_email(texto=corpo, marcadores=False))
    assert em.texto == "Where is my order?\nIt is late."
    assert em.pedido == PEDIDO_KFA


def test_so_o_marcador_de_fim_corta_o_rodape():
    """Início num formato desconhecido: pelo menos o rodapé do modelo não entra."""
    corpo = f"Olá, cadê meu pedido?\n------------- End message -------------\n{_RODAPE_AMAZON}\n"
    em = amazon_email.interpretar_email(_email(texto=corpo, marcadores=False))
    assert em.texto == "Olá, cadê meu pedido?"


def test_html_sem_texto_puro_vira_texto_sem_estilo_nem_citacao():
    html = (
        "<html><head><style>p{color:red}</style><title>x</title></head><body>"
        "<p>Oi,&nbsp;tudo bem?</p><div>O produto veio <b>sem</b> manual.<br>Obrigado</div>"
        "<blockquote>Mensagem antiga que não é desta vez</blockquote>"
        "</body></html>"
    )
    em = amazon_email.interpretar_email(_email(texto=None, html=html, marcadores=False))
    assert em.texto == "Oi, tudo bem?\nO produto veio sem manual.\nObrigado"
    assert "color" not in em.texto and "antiga" not in em.texto


def test_multipart_prefere_texto_puro_e_guarda_so_o_nome_do_anexo():
    bruto = _email(
        texto="Segue a foto do defeito.",
        html="<p>VERSÃO HTML</p>",
        anexo="foto do defeito.pdf",
    )
    em = amazon_email.interpretar_email(bruto)
    assert em.texto == "Segue a foto do defeito."
    assert "HTML" not in em.texto
    assert em.anexos == [{"tipo": "arquivo", "nome": "foto do defeito.pdf"}]


@pytest.mark.parametrize(
    "citacao",
    [
        "Em qua., 24 de set. de 2026 às 10:00, Loja <x@loja.com> escreveu:\n> resposta velha",
        "Em qua., 24 de set. de 2026 às 10:00, Loja <x@loja.com>\nescreveu:\n> velha",
        "On Wed, Sep 24, 2026 at 10:00 AM Loja <x@loja.com> wrote:\n> old answer",
        "-----Mensagem original-----\nDe: Loja\nresposta velha",
        "De: Loja <x@loja.com>\nEnviado: quarta-feira\nPara: fulano\nresposta velha",
    ],
)
def test_corta_historico_citado(citacao):
    texto = f"Em relação ao pedido, ainda não recebi.\n> linha citada solta\n\n{citacao}"
    assert amazon_email.extrair_mensagem(texto) == "Em relação ao pedido, ainda não recebi."


def test_so_remetente_de_retransmissao_e_comprador():
    assert amazon_email.interpretar_email(_email(de="Fulano <fulano@gmail.com>")) is None
    assert (
        amazon_email.interpretar_email(_email(de="<donotreply@marketplace.amazon.com.br>"))
        is None
    )
    # O falso "marketplace.amazon.com.br.golpe.com" não passa.
    assert not amazon_email.eh_endereco_de_retransmissao(
        "a@marketplace.amazon.com.br.golpe.com"
    )
    assert amazon_email.eh_endereco_de_retransmissao("abc@marketplace.amazon.com")
    assert amazon_email.eh_endereco_de_retransmissao("ABC@Marketplace.Amazon.com.br")


def test_sem_message_id_gera_id_estavel():
    bruto = _email(message_id=None)
    a = amazon_email.interpretar_email(bruto)
    b = amazon_email.interpretar_email(bruto)
    assert a.message_id.startswith("sem-id:")
    assert a.message_id == b.message_id  # a mesma releitura dá o mesmo id
    assert len(a.message_id) <= 191


def test_conta_pela_tag():
    kfa = Integration(name=" KFA ", archived_at=None)
    kia = Integration(name="Amazon K.I.A", archived_at=None)
    nexus = Integration(name="Nexus Amazon", archived_at=None)
    poofy1 = Integration(name="Poofy Loja", archived_at=None)
    poofy2 = Integration(name="Poofy Outlet", archived_at=None)
    todas = [kfa, kia, nexus, poofy1, poofy2]
    assert amazon_email.conta_pela_tag("kfa", todas) is kfa  # trim + minúsculas
    assert amazon_email.conta_pela_tag("amazonkia", todas) is kia  # sem pontuação
    assert amazon_email.conta_pela_tag("nexus", todas) is nexus  # palavra do nome
    assert amazon_email.conta_pela_tag("poofy", todas) is None  # duas casam: não chuta
    assert amazon_email.conta_pela_tag("outra", todas) is None


# ─────────────── resposta (função pura) ───────────────


def test_resposta_cabecalhos_da_thread_sem_re_duplo_e_com_pedido():
    msg = amazon_email.montar_resposta(
        remetente=CAIXA,
        para=RELAY_MARIA,
        assunto="RE: Re: Pergunta sobre o pedido",
        texto="Olá! Seu pedido já foi enviado.",
        pedido=PEDIDO_KFA,
        em_resposta_a="<m1@amazon.com.br>",
    )
    assert msg["From"] == CAIXA
    assert msg["To"] == RELAY_MARIA
    assert msg["Subject"] == "Re: Pergunta sobre o pedido"
    assert msg["In-Reply-To"] == "<m1@amazon.com.br>"
    assert msg["References"] == "<m1@amazon.com.br>"
    assert msg["Message-ID"].endswith("@loja-teste.com.br>")
    assert _corpo(msg.as_bytes()) == f"Olá! Seu pedido já foi enviado.\n\nPedido: {PEDIDO_KFA}"
    assert b"\r\n" in msg.as_bytes()  # SMTP quer CRLF

    # Pedido já no texto: não repete; sem e-mail anterior: sem In-Reply-To.
    msg2 = amazon_email.montar_resposta(
        remetente=CAIXA,
        para=RELAY_MARIA,
        assunto="Res: Dúvida",
        texto=f"O pedido {PEDIDO_KFA} saiu hoje.",
        pedido=PEDIDO_KFA,
        em_resposta_a=None,
    )
    assert msg2["Subject"] == "Re: Dúvida"
    assert msg2["In-Reply-To"] is None
    assert _corpo(msg2.as_bytes()).count(PEDIDO_KFA) == 1


def test_assunto_da_resposta_sem_assunto():
    assert amazon_email.assunto_da_resposta(None, PEDIDO_KFA) == f"Re: Pedido {PEDIDO_KFA}"
    assert amazon_email.assunto_da_resposta("  ", None) == "Re: Mensagem do vendedor"
    assert amazon_email.assunto_da_resposta("Re[2]: Oi", None) == "Re: Oi"


# ─────────────── sincronizar (caixa falsa + banco) ───────────────


async def test_sem_caixa_configurada_fica_desligado(
    db: AsyncSession, make_user, caixa_desligada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, CaixaFalsa({10: _email()}))
    user = await make_user()
    integ, canal = await _conta(db, user, "kfa")
    r = await amazon_email.sincronizar(db, canal, integ, None)
    assert r.status == "desligado"
    assert caixa.conexoes == 0
    assert redis_falso.dados == {}


async def test_primeira_rodada_so_leitura_e_cada_email_na_sua_conta(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, _caixa_da_rodada_1())
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "KFA")
    kia, canal_kia = await _conta(db, user, "kia", bling_loja_id=555)
    _nexus, _canal_nexus = await _conta(db, user, "nexus")
    # Uma Shopee chamada "kfa" não pode receber e-mail da Amazon.
    await _conta(db, user, "kfa", platform=IntegrationPlatform.SHOPEE)
    await _pedido_no_bling(db, PEDIDO_KIA, "555")

    r = await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()

    assert (r.status, r.conversas_novas, r.mensagens_novas) == ("ok", 3, 3)
    # Só leitura: EXAMINE (readonly) e BODY.PEEK (o falso barra outro FETCH).
    assert caixa.comandos("select") == [("select", '"INBOX"', True)]
    busca = caixa.comandos("uid")[0]
    desde = datetime.now(UTC).date() - timedelta(days=7)
    assert busca[1:3] == ("SEARCH", "SINCE")
    assert busca[3] == amazon_email._data_imap(desde)
    assert caixa.comandos("logout")

    conversas = {c.comprador_id: c for c in await _conversas(db)}
    assert set(conversas) == {RELAY_MARIA, RELAY_JOAO, RELAY_ANA}

    maria = conversas[RELAY_MARIA]  # pelo "+kfa"
    assert maria.integration_id == kfa.id and maria.canal_id == canal_kfa.id
    assert maria.conta == "KFA"
    assert maria.externo_id == f"{RELAY_MARIA}|{PEDIDO_KFA}"
    assert maria.comprador_nome == "Maria Silva"
    assert maria.pedido_marketplace == PEDIDO_KFA
    assert maria.dados == {
        "assunto": f"Pergunta sobre o pedido {PEDIDO_KFA}",
        "ultimo_message_id": "<m1@amazon.com.br>",
    }
    enviada = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)
    assert maria.aguardando_resposta is True
    assert maria.prazo_resposta_em == enviada + timedelta(hours=24)  # Amazon: 24 h corridas
    [m] = await _mensagens(db, maria.id)
    assert (m.externo_id, m.autor, m.origem) == ("<m1@amazon.com.br>", "cliente", "cliente")
    assert m.enviada_em == enviada
    assert m.texto == "Olá, meu pedido ainda não chegou."
    assert m.payload == {"imap_uid": 10, "conta_por": "tag"}

    joao = conversas[RELAY_JOAO]  # sem "+", pelo pedido no Bling (bling_loja_id)
    assert joao.integration_id == kia.id and joao.canal_id == canal_kia.id
    assert (await _mensagens(db, joao.id))[0].payload["conta_por"] == "pedido"

    ana = conversas[RELAY_ANA]  # nem "+", nem pedido conhecido
    assert ana.integration_id is None and ana.canal_id is None
    assert ana.conta == SEM_CONTA
    assert ana.aguardando_resposta is True  # sem dono, mas na fila: o prazo corre

    # O cursor é o MESMO nos três canais Amazon.
    assert await _cursores(db) == [{"uidvalidity": 777, "ultimo_uid": 14}] * 3
    assert redis_falso.dados[amazon_email.CHAVE_TRAVA] == "ok"
    assert redis_falso.ttl[amazon_email.CHAVE_TRAVA] == amazon_email.TRAVA_RODADA_S


async def test_rodadas_seguintes_andam_por_uid_sem_duplicar(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, CaixaFalsa({10: _email()}))
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    # O comprador responde na mesma thread — SEM o "+" (acha a conta pela
    # conversa anterior) e com o histórico citado embaixo.
    caixa.emails[20] = _email(
        para=CAIXA,
        assunto=f"RE: Pergunta sobre o pedido {PEDIDO_KFA}",
        data="Fri, 25 Sep 2026 09:00:00 -0300",
        message_id="<m9@amazon.com.br>",
        texto=(
            "Ainda nada, podem ver de novo?\n\n"
            "Em qui., 24 de set. de 2026 às 18:00, Loja <x@loja.com> escreveu:\n"
            "> Já enviamos."
        ),
        marcadores=False,
    )
    redis_falso.proxima_rodada()
    caixa.log.clear()
    r = await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    assert caixa.comandos("uid")[0] == ("uid", "SEARCH", "UID", "11:*")
    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (0, 1, 1)
    [conversa] = await _conversas(db)
    msgs = await _mensagens(db, conversa.id)
    assert [m.externo_id for m in msgs] == ["<m1@amazon.com.br>", "<m9@amazon.com.br>"]
    assert msgs[1].texto == "Ainda nada, podem ver de novo?"
    assert msgs[1].payload["conta_por"] == "conversa"
    # A resposta vai presa ao ÚLTIMO e-mail do comprador.
    assert conversa.dados["ultimo_message_id"] == "<m9@amazon.com.br>"
    assert conversa.dados["assunto"] == f"RE: Pergunta sobre o pedido {PEDIDO_KFA}"
    assert await _cursores(db) == [{"uidvalidity": 777, "ultimo_uid": 20}]

    # Nada novo: o IMAP devolve o maior UID para `21:*` — não pode reprocessar.
    redis_falso.proxima_rodada()
    caixa.log.clear()
    r = await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    assert caixa.comandos("uid") == [("uid", "SEARCH", "UID", "21:*")]  # nem FETCH
    assert r.mensagens_novas == 0
    assert len(await _mensagens(db, conversa.id)) == 2


async def test_email_atrasado_nao_volta_a_thread(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa({10: _email(data="Fri, 25 Sep 2026 09:00:00 -0300", message_id="<novo@a>")}),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    caixa.emails[11] = _email(data="Wed, 23 Sep 2026 09:00:00 -0300", message_id="<velho@a>")
    redis_falso.proxima_rodada()
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    [conversa] = await _conversas(db)
    assert len(await _mensagens(db, conversa.id)) == 2
    assert conversa.dados["ultimo_message_id"] == "<novo@a>"


async def test_uidvalidity_mudou_recomeca_pelos_7_dias_sem_duplicar(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, _caixa_da_rodada_1())
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    antes = await db.scalar(select(func.count()).select_from(AtendimentoMensagem))

    # Caixa migrada: os mesmos e-mails com UIDs novos e outra UIDVALIDITY.
    caixa.emails = {i + 1: bruto for i, bruto in enumerate(caixa.emails.values())}
    caixa.uidvalidity = 888
    redis_falso.proxima_rodada()
    caixa.log.clear()
    r = await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    assert caixa.comandos("uid")[0][1:3] == ("SEARCH", "SINCE")
    assert r.mensagens_novas == 0  # idempotente pelo Message-ID
    assert await db.scalar(select(func.count()).select_from(AtendimentoMensagem)) == antes
    assert await _cursores(db) == [{"uidvalidity": 888, "ultimo_uid": 5}]


async def test_caixa_lida_uma_vez_por_rodada_com_cursor_do_banco(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, _caixa_da_rodada_1())
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "kfa")
    kia, canal_kia = await _conta(db, user, "kia", bling_loja_id=555)

    # O canal kia é carregado ANTES da leitura (cursor vazio na memória), como
    # no sync de verdade, em que cada canal roda na sua sessão.
    async with _db.SessionLocal() as outra:
        canal_kia_velho = await outra.get(AtendimentoCanal, canal_kia.id)
        kia_outra = await outra.get(Integration, kia.id)
        assert canal_kia_velho.cursor == {}

        await amazon_email.sincronizar(db, canal_kfa, kfa, None)
        await db.commit()
        assert caixa.conexoes == 1

        # Mesma rodada: o canal kia NÃO abre a caixa; devolve o resultado dela.
        r = await amazon_email.sincronizar(outra, canal_kia_velho, kia_outra, None)
        assert r.status == "ok" and r.mensagens_novas == 0
        assert caixa.conexoes == 1

        # Rodada seguinte, lida pelo kia: o cursor vem do BANCO (o objeto em
        # memória ainda diz {}), então segue do UID 14 em vez de reler 7 dias.
        redis_falso.proxima_rodada()
        caixa.log.clear()
        r = await amazon_email.sincronizar(outra, canal_kia_velho, kia_outra, None)
        await outra.commit()
        assert caixa.conexoes == 2
        assert caixa.comandos("uid") == [("uid", "SEARCH", "UID", "15:*")]
        assert r.mensagens_novas == 0

    assert await _cursores(db) == [{"uidvalidity": 777, "ultimo_uid": 14}] * 2


async def test_outro_canal_lendo_agora_nao_abre_a_caixa(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, _caixa_da_rodada_1())
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")

    redis_falso.dados[amazon_email.CHAVE_TRAVA] = "lendo"
    r = await amazon_email.sincronizar(db, canal, kfa, None)
    assert (r.status, r.mensagens_novas) == ("ok", 0)
    assert caixa.conexoes == 0
    assert redis_falso.dados[amazon_email.CHAVE_TRAVA] == "lendo"  # a trava não é minha

    # O leitor da rodada falhou: os outros canais mostram o mesmo erro.
    redis_falso.dados[amazon_email.CHAVE_TRAVA] = "erro:imap error: caixa fora"
    r = await amazon_email.sincronizar(db, canal, kfa, None)
    assert (r.status, r.erro) == ("erro", "imap error: caixa fora")
    assert caixa.conexoes == 0


async def test_erro_de_imap_vira_status_e_vale_para_a_rodada(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    caixa = _instalar_caixa(monkeypatch, _caixa_da_rodada_1())
    caixa.erro_login = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "kfa")
    kia, canal_kia = await _conta(db, user, "kia")

    r = await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    assert r.status == "erro"
    assert "AUTHENTICATIONFAILED" in r.erro
    assert "senha-de-app" not in r.erro
    assert caixa.comandos("logout")  # a conexão fecha mesmo no erro

    r2 = await amazon_email.sincronizar(db, canal_kia, kia, None)
    assert r2.status == "erro" and "AUTHENTICATIONFAILED" in r2.erro
    assert caixa.conexoes == 1  # não bate de novo no servidor na mesma rodada
    await db.commit()
    assert await _cursores(db) == [{}, {}]  # o cursor não anda


async def test_excecao_na_gravacao_solta_a_trava(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    _instalar_caixa(monkeypatch, _caixa_da_rodada_1())
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")

    async def _quebra(session, leitura):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(amazon_email, "_gravar_leitura", _quebra)
    with pytest.raises(RuntimeError):
        await amazon_email.sincronizar(db, canal, kfa, None)
    # Sem marca de "já lida": o próximo canal/rodada tenta de novo.
    assert amazon_email.CHAVE_TRAVA not in redis_falso.dados


async def test_conta_pelo_pedido_via_loja_e_ficha_da_loja(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """Sem "+": o pedido no espelho do Bling leva à conta por três caminhos."""
    pedido_store, pedido_ficha, pedido_nome = (
        "704-4444444-4444444",
        "705-5555555-5555555",
        "706-6666666-6666666",
    )
    _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(de=f"A <{RELAY_MARIA}>", para=CAIXA, message_id="<a@a>",
                           assunto=f"Pedido {pedido_store}"),
                11: _email(de=f"B <{RELAY_JOAO}>", para=CAIXA, message_id="<b@a>",
                           assunto=f"Pedido {pedido_ficha}"),
                12: _email(de=f"C <{RELAY_ANA}>", para=CAIXA, message_id="<c@a>",
                           assunto=f"Pedido {pedido_nome}"),
            }
        ),
    )
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "kfa")
    kia, _ = await _conta(db, user, "kia")
    poofy, _ = await _conta(db, user, "poofy", com_canal=False)  # conta sem canal
    nexus, _ = await _conta(db, user, "Nexus")

    # 1) loja do Bling → Store (bling_store_id) → integração da loja.
    empresa = Company(razao_social="Empresa Teste LTDA", apelido="teste")
    db.add(empresa)
    await db.flush()
    db.add(Store(company_id=empresa.id, marketplace=Marketplace.AMAZON,
                 bling_store_id=111, integration_id=kia.id))
    # 2) ficha da loja (StoreInfo) com a integração ligada.
    db.add(StoreInfo(user_id=user.id, platform="amazon", bling_store_id="321",
                     integration_id=poofy.id))
    # 3) ficha sem integração: vale o nome da conta.
    db.add(StoreInfo(user_id=user.id, platform="amazon", bling_store_id="654",
                     account_name="NEXUS"))
    await db.commit()
    await _pedido_no_bling(db, pedido_store, "111")
    await _pedido_no_bling(db, pedido_ficha, "321")
    await _pedido_no_bling(db, pedido_nome, "654")

    r = await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()
    assert r.mensagens_novas == 3
    por_pedido = {c.pedido_marketplace: c for c in await _conversas(db)}
    assert por_pedido[pedido_store].integration_id == kia.id
    assert por_pedido[pedido_ficha].integration_id == poofy.id
    assert por_pedido[pedido_ficha].canal_id is None  # conta sem canal: entra igual
    assert por_pedido[pedido_ficha].conta == "poofy"
    assert por_pedido[pedido_nome].integration_id == nexus.id


async def _nossa_resposta(
    db: AsyncSession,
    conversa: AtendimentoConversa,
    message_id: str,
    *,
    status: str = "enviada",
    no_payload: bool = False,
    enviada_em: datetime = datetime(2026, 9, 24, 14, 0, tzinfo=UTC),
) -> AtendimentoMensagem:
    """A resposta que o envio (lote E) gravou: aceita = Message-ID no `externo_id`;
    ambígua (`revisar`) = só no payload do envio."""
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=None if no_payload else message_id,
        autor="loja",
        origem="davinci_humano",
        texto="Seu pedido chega amanhã.",
        status=status,
        enviada_em=enviada_em,
        payload={"envio": {"message_id": message_id, "smtp": None}},
    )
    db.add(nossa)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    return nossa


async def _rodada(db, canal, integ, redis_falso, caixa, *uids_emails):
    for uid, bruto in uids_emails:
        caixa.emails[uid] = bruto
    redis_falso.proxima_rodada()
    r = await amazon_email.sincronizar(db, canal, integ, None)
    await db.commit()
    return r


async def test_email_seguinte_sem_pedido_entra_na_conversa_da_thread(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """API-11: a chave era `<retransmissão>|<pedido>` — o e-mail seguinte sem o
    701-... abria OUTRA conversa, "sem conta" e impossível de responder."""
    # Datas perto de agora: o endereço sozinho só vale para conversa recente.
    agora = datetime.now(UTC).replace(microsecond=0)

    def _quando(horas: int) -> str:
        return format_datetime(agora - timedelta(hours=horas))

    caixa = _instalar_caixa(monkeypatch, CaixaFalsa({10: _email(data=_quando(4))}))
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    [conversa] = await _conversas(db)
    await _nossa_resposta(
        db, conversa, "<nossa1@loja-teste.com.br>", enviada_em=agora - timedelta(hours=3)
    )

    # 1) Resposta à NOSSA resposta: sem "+", sem pedido, com In-Reply-To.
    resposta_a_nossa = _email(
        para=CAIXA,
        assunto="Re: Sua mensagem",
        data=_quando(2),
        message_id="<m11@amazon.com.br>",
        texto="Obrigada! E a nota fiscal?",
        marcadores=False,
        em_resposta_a="<nossa1@loja-teste.com.br>",
    )
    await _rodada(db, canal, kfa, redis_falso, caixa, (11, resposta_a_nossa))
    # 2) Sem In-Reply-To nenhum: o mesmo endereço de retransmissão, conversa recente.
    sem_referencia = _email(
        para=CAIXA,
        assunto="Outra dúvida",
        message_id="<m12@amazon.com.br>",
        data=_quando(1),
        texto="Vem com manual?",
        marcadores=False,
    )
    await _rodada(db, canal, kfa, redis_falso, caixa, (12, sem_referencia))

    [conversa] = await _conversas(db)  # nenhuma conversa "|-" nova
    assert conversa.externo_id == f"{RELAY_MARIA}|{PEDIDO_KFA}"
    assert conversa.integration_id == kfa.id
    msgs = await _mensagens(db, conversa.id)
    clientes = [m for m in msgs if m.autor == "cliente"]
    assert [m.externo_id for m in clientes] == [
        "<m1@amazon.com.br>",
        "<m11@amazon.com.br>",
        "<m12@amazon.com.br>",
    ]
    assert [m.payload["conta_por"] for m in clientes[1:]] == ["thread", "thread"]
    assert conversa.aguardando_resposta is True
    assert conversa.dados["ultimo_message_id"] == "<m12@amazon.com.br>"


async def test_conversa_sem_conta_e_adotada_quando_a_conta_aparece(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """DADOS-11: o 1º e-mail (sem "+", pedido fora do Bling) nasce "sem conta";
    o 2º da mesma thread, com o pedido já no Bling, criava OUTRA conversa — e a
    primeira ficava aguardando para sempre, sem poder ser respondida."""
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa({10: _email(para=CAIXA, assunto=f"Pedido {PEDIDO_KIA}", texto="Oi?")}),
    )
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "kfa")
    kia, canal_kia = await _conta(db, user, "kia", bling_loja_id=555)
    await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()
    [sem_conta] = await _conversas(db)
    assert (sem_conta.integration_id, sem_conta.conta) == (None, SEM_CONTA)

    await _pedido_no_bling(db, PEDIDO_KIA, "555")
    seguinte = _email(
        para=CAIXA,
        assunto=f"RE: Pedido {PEDIDO_KIA}",
        message_id="<m2@a>",
        data="Thu, 24 Sep 2026 12:00:00 -0300",
        texto="Alguém?",
    )
    await _rodada(db, canal_kfa, kfa, redis_falso, caixa, (11, seguinte))

    [conversa] = await _conversas(db)
    assert conversa.id == sem_conta.id  # a MESMA conversa, agora da conta certa
    assert (conversa.integration_id, conversa.canal_id, conversa.conta) == (
        kia.id,
        canal_kia.id,
        "kia",
    )
    assert len(await _mensagens(db, conversa.id)) == 2
    assert conversa.aguardando_resposta is True


async def test_duas_conversas_da_mesma_thread_sao_juntadas(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """Se a duplicata já existe (sem conta + da conta), a sem conta é juntada e fecha."""
    caixa = _instalar_caixa(monkeypatch, CaixaFalsa())
    user = await make_user()
    kia, canal_kia = await _conta(db, user, "kia", bling_loja_id=555)
    chave = f"{RELAY_JOAO}|{PEDIDO_KIA}"
    orfa = AtendimentoConversa(
        plataforma="amazon",
        canal="email",
        externo_id=chave,
        conta=SEM_CONTA,
        comprador_id=RELAY_JOAO,
    )
    dona = AtendimentoConversa(
        plataforma="amazon",
        canal="email",
        externo_id=chave,
        integration_id=kia.id,
        canal_id=canal_kia.id,
        conta="kia",
        comprador_id=RELAY_JOAO,
    )
    db.add_all([orfa, dona])
    await db.flush()
    for conversa, mid, hora in ((orfa, "<o1@a>", 9), (dona, "<d1@a>", 10)):
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=mid,
            autor="cliente",
            texto="Oi",
            enviada_em=datetime(2026, 9, 24, hora, 0, tzinfo=UTC),
        )
    await db.commit()
    await _pedido_no_bling(db, PEDIDO_KIA, "555")

    novo = _email(
        de=f"João <{RELAY_JOAO}>",
        para=CAIXA,
        assunto=f"Pedido {PEDIDO_KIA}",
        message_id="<j3@a>",
        data="Thu, 24 Sep 2026 11:00:00 -0300",
    )
    await _rodada(db, canal_kia, kia, redis_falso, caixa, (10, novo))

    await db.refresh(orfa)
    await db.refresh(dona)
    assert [m.externo_id for m in await _mensagens(db, dona.id)] == ["<o1@a>", "<d1@a>", "<j3@a>"]
    assert await _mensagens(db, orfa.id) == []
    assert (orfa.situacao, orfa.aguardando_resposta) == ("fechada", False)
    assert orfa.dados["juntada_a"] == str(dona.id)
    assert dona.aguardando_resposta is True


def _devolucao_do_servidor(message_id_original: str, status: str = "5.7.1") -> bytes:
    """Relatório de entrega (RFC 3464) do NOSSO provedor, citando os cabeçalhos."""
    return (
        "From: Mail Delivery System <MAILER-DAEMON@smtp.teste.local>\r\n"
        f"To: {CAIXA}\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
        "Date: Thu, 24 Sep 2026 14:05:00 -0300\r\n"
        "Message-ID: <dsn1@smtp.teste.local>\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="XYZ"\r\n'
        "\r\n"
        "--XYZ\r\n"
        "Content-Type: text/plain; charset=us-ascii\r\n\r\n"
        "This is the mail system. Your message could not be delivered.\r\n"
        "--XYZ\r\n"
        "Content-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; smtp.teste.local\r\n\r\n"
        f"Final-Recipient: rfc822; {RELAY_MARIA}\r\n"
        "Action: failed\r\n"
        f"Status: {status}\r\n"
        "Diagnostic-Code: smtp; 550 sender not authorized\r\n\r\n"
        "--XYZ\r\n"
        "Content-Type: text/rfc822-headers\r\n\r\n"
        f"From: {CAIXA}\r\n"
        f"To: {RELAY_MARIA}\r\n"
        "Subject: Re: Pergunta\r\n"
        f"Message-ID: {message_id_original}\r\n"
        "\r\n"
        "--XYZ--\r\n"
    ).encode()


async def test_devolucao_e_recusa_da_amazon_viram_falhou_e_voltam_a_fila(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """API-8: o 250 do NOSSO SMTP virava "enviada" para sempre — a recusa que
    chega depois (servidor ou Amazon) nunca era lida."""
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(),
                11: _email(
                    de=f"João <{RELAY_JOAO}>",
                    assunto=f"Pedido {PEDIDO_KIA}",
                    message_id="<m2@amazon.com.br>",
                ),
                12: _email(
                    de=f"Ana <{RELAY_ANA}>",
                    assunto=f"Pedido {PEDIDO_SEM_DONO}",
                    message_id="<m3@amazon.com.br>",
                ),
            }
        ),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    por_comprador = {c.comprador_id: c for c in await _conversas(db)}
    maria, joao, ana = (por_comprador[r] for r in (RELAY_MARIA, RELAY_JOAO, RELAY_ANA))
    n1 = await _nossa_resposta(db, maria, "<nossa1@loja-teste.com.br>")
    n2 = await _nossa_resposta(db, joao, "<nossa2@loja-teste.com.br>")
    n3 = await _nossa_resposta(
        db, ana, "<nossa3@loja-teste.com.br>", status="revisar", no_payload=True
    )
    assert maria.aguardando_resposta is False

    aviso_amazon = _email(
        de="Amazon <donotreply@amazon.com.br>",
        para=CAIXA,
        assunto="Sua mensagem não foi entregue",
        message_id="<aviso9@amazon.com.br>",
        texto="Não foi possível entregar a sua mensagem ao comprador.",
        marcadores=False,
        em_resposta_a="<nossa2@loja-teste.com.br>",
    )
    atraso = _devolucao_do_servidor("<nossa3@loja-teste.com.br>", status="4.4.1")
    r = await _rodada(
        db,
        canal,
        kfa,
        redis_falso,
        caixa,
        (20, _devolucao_do_servidor("<nossa1@loja-teste.com.br>")),
        (21, aviso_amazon),
        (22, atraso),  # 4.x.x: o servidor ainda está tentando — não é falha
    )

    for m in (n1, n2, n3):
        await db.refresh(m)
    assert (n1.status, n1.erro) == ("falhou", "email devolvido 5.7.1")
    assert (n2.status, n2.erro) == ("falhou", "email recusado pela Amazon")
    assert n3.status == "revisar"
    assert r.conversas_atualizadas == 2
    for conversa in (maria, joao):
        await db.refresh(conversa)
        assert conversa.aguardando_resposta is True  # o comprador não recebeu nada
    # Aviso não é mensagem de comprador: nenhuma conversa nova.
    assert len(await _conversas(db)) == 3

    # A devolução da ambígua (só no payload do envio) casa também.
    devolvida = _devolucao_do_servidor("<nossa3@loja-teste.com.br>")
    await _rodada(db, canal, kfa, redis_falso, caixa, (23, devolvida))
    await db.refresh(n3)
    assert (n3.status, n3.erro) == ("falhou", "email devolvido 5.7.1")


# ─────────────── enviar_texto (SMTP falso) ───────────────


def _conversa_amazon(**extra) -> AtendimentoConversa:
    campos = {
        "plataforma": "amazon",
        "canal": "email",
        "externo_id": f"{RELAY_MARIA}|{PEDIDO_KFA}",
        "comprador_id": RELAY_MARIA,
        "pedido_marketplace": PEDIDO_KFA,
        "dados": {"assunto": "Re: Pergunta", "ultimo_message_id": "<m1@amazon.com.br>"},
    }
    campos.update(extra)
    return AtendimentoConversa(**campos)


def _instalar_smtp(monkeypatch, smtp: SmtpFalso) -> SmtpFalso:
    monkeypatch.setattr(amazon_email, "_abrir_smtp", lambda config: smtp)
    return smtp


async def test_enviar_sem_caixa_configurada(caixa_desligada, monkeypatch):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo, r.erro) == (False, False, "caixa_nao_configurada")
    assert smtp.chamadas == []


async def test_enviar_sem_endereco_de_retransmissao(caixa_configurada, monkeypatch):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    conversa = _conversa_amazon(comprador_id="fulano@gmail.com")
    r = await amazon_email.enviar_texto(None, conversa, None, None, "Oi")
    assert (r.ok, r.erro) == (False, "sem_endereco_de_retransmissao")
    assert smtp.chamadas == []


async def test_enviar_ok_entrega_na_thread(caixa_configurada, monkeypatch):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    r = await amazon_email.enviar_texto(
        None, _conversa_amazon(), None, None, "Olá! Já foi enviado."
    )
    assert r.ok is True and r.ambiguo is False
    assert smtp.chamadas[:4] == ["ehlo", "starttls", "ehlo", ("login", CAIXA)]
    assert ("mail", CAIXA) in smtp.chamadas and ("rcpt", RELAY_MARIA) in smtp.chamadas
    enviado = message_from_bytes(smtp.enviado, policy=politica_email.default)
    assert r.externo_id == enviado["Message-ID"]  # o nosso id vira o externo_id
    assert r.payload["message_id"] == r.externo_id
    assert enviado["From"] == CAIXA and enviado["To"] == RELAY_MARIA
    assert enviado["Subject"] == "Re: Pergunta"  # não vira "Re: Re:"
    assert enviado["In-Reply-To"] == "<m1@amazon.com.br>"
    assert _corpo(smtp.enviado) == f"Olá! Já foi enviado.\n\nPedido: {PEDIDO_KFA}"
    assert "quit" in smtp.chamadas


async def test_enviar_recusa_do_servidor_nao_e_ambigua(caixa_configurada, monkeypatch):
    smtp = _instalar_smtp(monkeypatch, SmtpFalso(rcpt=(550, b"5.1.1 mailbox unavailable")))
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo) == (False, False)
    assert "550" in r.erro
    assert "data" not in smtp.chamadas  # não chegou ao DATA

    _instalar_smtp(monkeypatch, SmtpFalso(data=(554, b"5.7.1 rejected")))
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo) == (False, False) and "554" in r.erro

    _instalar_smtp(
        monkeypatch,
        SmtpFalso(erro_em="data", erro=smtplib.SMTPDataError(451, b"try later")),
    )
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo) == (False, False) and "451" in r.erro

    _instalar_smtp(
        monkeypatch,
        SmtpFalso(
            erro_em="login",
            erro=smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        ),
    )
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo) == (False, False) and "535" in r.erro


@pytest.mark.parametrize(
    "erro",
    [TimeoutError("timed out"), smtplib.SMTPServerDisconnected("Connection unexpectedly closed")],
)
async def test_queda_depois_do_data_e_ambigua(caixa_configurada, monkeypatch, erro):
    _instalar_smtp(monkeypatch, SmtpFalso(erro_em="data", erro=erro))
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo) == (False, True)
    assert r.externo_id is None
    assert r.payload["message_id"].startswith("<")  # para a pessoa conferir


async def test_queda_antes_do_data_nao_e_ambigua(caixa_configurada, monkeypatch):
    def _recusa(config):
        raise ConnectionRefusedError("recusado")

    monkeypatch.setattr(amazon_email, "_abrir_smtp", _recusa)
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert (r.ok, r.ambiguo) == (False, False)
    assert "conexao" in r.erro


async def test_quit_falhando_depois_de_aceito_ainda_e_ok(caixa_configurada, monkeypatch):
    smtp = _instalar_smtp(
        monkeypatch, SmtpFalso(erro_no_quit=smtplib.SMTPServerDisconnected("bye"))
    )
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert r.ok is True
    assert "close" in smtp.chamadas


class _SettingsComSmtp:
    """Os settings de verdade + o par de SMTP (o núcleo acrescenta os campos)."""

    def __init__(self, base, **extra):
        self._base, self._extra = base, extra

    def __getattr__(self, nome):
        if nome in self._extra:
            return self._extra[nome]
        return getattr(self._base, nome)


async def test_smtp_usa_usuario_e_senha_proprios_quando_configurados(
    caixa_configurada, monkeypatch
):
    """D8: com `atendimento_amazon_smtp_usuario/_senha`, o login do SMTP usa
    esse par; vazios, vale o do IMAP."""
    smtp = _instalar_smtp(monkeypatch, SmtpFalso())
    proprios = _SettingsComSmtp(
        caixa_configurada,
        atendimento_amazon_smtp_usuario="envio@loja-teste.com.br",
        atendimento_amazon_smtp_senha="senha-do-smtp",
    )
    monkeypatch.setattr(amazon_email, "get_settings", lambda: proprios)
    config = amazon_email.configuracao()
    assert (config.smtp_usuario, config.smtp_senha) == ("envio@loja-teste.com.br", "senha-do-smtp")
    assert (config.usuario, config.senha) == (CAIXA, "senha-de-app")  # o IMAP não muda
    assert "senha-do-smtp" not in repr(config)
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert r.ok is True
    assert ("login", "envio@loja-teste.com.br") in smtp.chamadas

    vazios = _SettingsComSmtp(
        caixa_configurada, atendimento_amazon_smtp_usuario="", atendimento_amazon_smtp_senha=""
    )
    monkeypatch.setattr(amazon_email, "get_settings", lambda: vazios)
    smtp.chamadas.clear()
    r = await amazon_email.enviar_texto(None, _conversa_amazon(), None, None, "Oi")
    assert r.ok is True
    assert ("login", CAIXA) in smtp.chamadas


# ─────────────── formato REAL (28/09/2026): marcadores, rodapé, cópia ───────────────
# Montado a partir das amostras mascaradas: o FORMATO é o do e-mail de
# verdade; nomes, textos, ids, hash e links são inventados.

CASO = "11111111-2222-4333-8444-555555555555"
RELAY_CASO = f"anonimo42+{CASO}@marketplace.amazon.com.br"
LINK_SEM_RESPOSTA = (
    "https://sellercentral.amazon.com.br/messaging/no-response-needed"
    "?t=ATESTE0000000000001&m=ATESTE0000000000002&mp=A2Q3Y263D00KWC"
    f"&c=AVENDEDORTESTE&h={'0' * 40}&s=1"
)
LINK_DENUNCIA = (
    "https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId"
    f"&ss={CASO}&cc={CASO}&ref=bsm_email_report&lc=pt_BR"
)
LINK_CASO = (
    f"https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId&ss={CASO}&cc={CASO}"
)
LINK_PESQUISA = (
    "https://sellercentral.amazon.com.br/gp/satisfaction/survey-form.html?ie=UTF8"
    "&HMDName=NotificationBusEmailHMD&customAttribute1Value=BBC_MESSAGE_SENT_TO_MERCHANT"
)


def _corpo_real(msg1: str, msg2: str, *, com_links: bool = True) -> str:
    """O text/plain do e-mail do comprador, como veio (dois espaços em "Mensagem:")."""
    rodape = (
        f"Este e-mail foi útil? {LINK_PESQUISA}\n\n"
        f"Solucionar o caso {LINK_SEM_RESPOSTA}\n\n"
        f"Denunciar atividade questionável {LINK_DENUNCIA}\n\n"
        if com_links
        else ""
    )
    return (
        "Você recebeu uma mensagem.\n\n\n"
        "------------- Mensagem:  -------------\n\n"
        f"{msg1} \n \n{msg2}\n\n\n"
        "------------- Encerrar mensagem -------------\n\n"
        f"{rodape}"
        "--------------------------\n\n"
        "Direitos autorais 2026 Amazon, Inc. ou suas empresas afiliadas.\n\n"
        f"{_RODAPE_AMAZON}\n\n"
        "SPC-USAmazon-0000000000000000\n"
    )


def _email_real(
    *,
    de: str = f'"Maria Silva" <{RELAY_CASO}>',
    para: str = "atendimento+kfa@loja-teste.com.br",
    data: str = "Mon, 28 Sep 2026 09:00:00 -0300",
    message_id: str = "<real1@amazon.com.br>",
    msg1: str = "Olá, a mala tem cadeado?",
    msg2: str = "E cabe no bagageiro do avião?",
    com_links: bool = True,
    em_resposta_a: str | None = None,
) -> bytes:
    """O e-mail do comprador no formato real: text/plain + text/html."""
    msg = EmailMessage()
    msg["From"] = de
    msg["To"] = para
    msg["Subject"] = "Detalhes do produto Amazon Maria Silva pergunta do cliente"
    msg["Date"] = data
    msg["Message-ID"] = message_id
    msg["X-Space-Notification-Type"] = "BBC_MESSAGE_SENT_TO_MERCHANT"
    msg["X-Marketplace-ID"] = "A2Q3Y263D00KWC"
    if em_resposta_a:
        msg["In-Reply-To"] = em_resposta_a
    msg.set_content(_corpo_real(msg1, msg2, com_links=com_links))
    link_html = (
        f'<a href="{LINK_SEM_RESPOSTA.replace("&", "&amp;")}">Solucionar o caso</a>'
        if com_links
        else ""
    )
    msg.add_alternative(
        f"<html><body><p>{msg1}</p><p>{msg2}</p>{link_html}</body></html>", subtype="html"
    )
    return msg.as_bytes()


def _html_confirmacao(nome: str, resposta: str, *, em_blocos: bool = False) -> str:
    """O HTML da cópia. `em_blocos=False` = tudo num parágrafo, como a amostra
    convertida; `True` = tabela, parágrafos e a resposta num blockquote."""
    resposta_html = resposta.replace("\n", "<br>")
    if em_blocos:
        miolo = (
            "<table><tr><td><p>Prezado Loja Teste,</p>"
            f"<p>Aqui está uma cópia do e-mail que você enviou para {nome}.</p>"
            "<p>------------- Iniciar mensagem -------------</p>"
            f"<blockquote>{resposta_html}</blockquote>"
            "<p>------------- Mensagem final -------------</p>"
            "<p>Atenciosamente,<br>Amazon.com.br</p></td></tr></table>"
        )
    else:
        miolo = (
            f"<div>Prezado Loja Teste, Aqui está uma cópia do e-mail que você enviou para {nome}. "
            f"------------- Iniciar mensagem ------------- {resposta_html} "
            "------------- Mensagem final ------------- Atenciosamente, Amazon.com.br "
            '<a href="http://www.amazon.com.br">http://www.amazon.com.br</a> '
            f"{_RODAPE_AMAZON} Este e-mail foi útil? [commMgrTok:ATESTE0000000000003]</div>"
        )
    return f"<html><head><style>td{{color:#333}}</style></head><body>{miolo}</body></html>"


def _confirmacao(
    *,
    nome: str = "Maria Silva",
    resposta: str = "Olá, Maria!\nTem cadeado TSA e cabe, sim.",
    para: str = "atendimento+kfa@loja-teste.com.br",
    data: str = "Mon, 28 Sep 2026 10:00:00 -0300",
    message_id: str = "<conf1@amazon.com>",
    tipo: str | None = "BBC_MESSAGE_CONFIRMATION_TO_MERCHANT",
    assunto: str | None = None,
    de: str = '"Amazon.com.br" <donotreply@amazon.com>',
    em_blocos: bool = False,
    autenticacao: str | None = None,
) -> bytes:
    """A cópia da resposta dada no Seller Central: só HTML, sem caso nem relay."""
    msg = EmailMessage()
    msg["From"] = de
    msg["To"] = para
    msg["Reply-To"] = "naoresponda@amazon.com"
    msg["Subject"] = assunto if assunto is not None else f"Seu e-mail para {nome}"
    msg["Date"] = data
    msg["Message-ID"] = message_id
    if tipo:
        msg["X-Space-Notification-Type"] = tipo
    msg["X-Marketplace-ID"] = "A2Q3Y263D00KWC"
    if autenticacao:
        msg["Authentication-Results"] = autenticacao
    msg.set_content(_html_confirmacao(nome, resposta, em_blocos=em_blocos), subtype="html")
    return msg.as_bytes()


def test_email_real_texto_so_o_miolo_e_links_do_rodape():
    """1º e-mail real: "Encerrar mensagem" não era conhecido e o texto gravado
    levou o rodapé inteiro (1.374 caracteres, com três links)."""
    em = amazon_email.interpretar_email(_email_real(), uid=30)
    assert em is not None
    assert em.texto == "Olá, a mala tem cadeado?\n\nE cabe no bagageiro do avião?"
    assert "http" not in em.texto and "Solucionar" not in em.texto
    assert em.remetente == RELAY_CASO  # o "+caso" fica: é o endereço de resposta
    assert em.remetente_nome == "Maria Silva"
    assert em.tag == "kfa"
    assert em.pedido is None  # pergunta pré-venda
    assert em.link_sem_resposta == LINK_SEM_RESPOSTA  # assinado: guardado como veio
    assert em.caso_id == CASO  # do `cc` do link de denúncia
    assert em.link_caso == LINK_CASO


_T = "-------------"


@pytest.mark.parametrize(
    ("inicio", "fim"),
    [
        (f"{_T} Mensagem:  {_T}", f"{_T} Encerrar mensagem {_T}"),  # comprador (real)
        (f"{_T} Iniciar mensagem {_T}", f"{_T} Mensagem final {_T}"),  # cópia (real)
        (f"{_T}   Iniciar   mensagem   {_T}", f"{_T}Encerrar  mensagem---"),
        (f"{_T} Mensagem: {_T}", f"{_T} Fim da mensagem {_T}"),
        (f"{_T} Begin message {_T}", f"{_T} End message {_T}"),
    ],
)
def test_marcadores_reais_com_espacos_variaveis(inicio, fim):
    corpo = (
        f"Você recebeu uma mensagem.\n\n{inicio}\n\nTem na cor azul?\n\n{fim}\n\n"
        f"{_RODAPE_AMAZON}"
    )
    assert amazon_email.extrair_mensagem(corpo) == "Tem na cor azul?"
    # HTML convertido pode deixar tudo na MESMA linha: vale igual.
    corrido = f"Prezado vendedor, {inicio} Tem na cor azul? {fim} {_RODAPE_AMAZON}"
    assert amazon_email.extrair_mensagem(corrido) == "Tem na cor azul?"


def test_sem_marcador_de_fim_corta_no_rodape_e_tira_os_links():
    corpo = _corpo_real("Tem na cor azul?", "Obrigada.").replace(
        "------------- Encerrar mensagem -------------", ""
    )
    assert amazon_email.extrair_mensagem(corpo) == "Tem na cor azul?\n\nObrigada."
    # Link do Seller Central no meio do texto (formato desconhecido) nunca fica.
    solto = f"Olha isso {LINK_SEM_RESPOSTA} e responde"
    assert amazon_email.extrair_mensagem(solto) == "Olha isso e responde"


def test_caso_pelo_mais_do_remetente_quando_nao_ha_link():
    em = amazon_email.interpretar_email(_email_real(com_links=False))
    assert em.link_sem_resposta is None
    assert em.caso_id == CASO  # o que vem depois do "+" no From
    assert em.link_caso == LINK_CASO
    # Relay sem "+" e sem link: sem caso (nada inventado).
    sem = amazon_email.interpretar_email(_email())
    assert (sem.caso_id, sem.link_caso, sem.link_sem_resposta) == (None, None, None)


def test_links_so_no_html_e_host_falso_nao_passa():
    falso = LINK_SEM_RESPOSTA.replace("amazon.com.br/", "amazon.com.br.golpe.com/")
    html = (
        "<html><body><p>------------- Mensagem: -------------</p><p>Oi?</p>"
        "<p>------------- Encerrar mensagem -------------</p>"
        f'<a href="{falso.replace("&", "&amp;")}">Solucionar</a>'
        f'<a href="{LINK_SEM_RESPOSTA.replace("&", "&amp;")}">Solucionar o caso</a>'
        f'<a href="{LINK_DENUNCIA.replace("&", "&amp;")}">Denunciar</a></body></html>'
    )
    em = amazon_email.interpretar_email(
        _email(de=f"Maria <{RELAY_MARIA}>", texto=None, html=html, marcadores=False)
    )
    assert em.texto == "Oi?"
    assert em.link_sem_resposta == LINK_SEM_RESPOSTA  # `&amp;` desfeito; o falso, fora
    assert em.caso_id == CASO


@pytest.mark.parametrize("em_blocos", [False, True])
def test_copia_da_resposta_interpretada(em_blocos):
    bruto = _confirmacao(em_blocos=em_blocos)
    assert amazon_email.interpretar_email(bruto) is None  # não é comprador
    assert amazon_email.interpretar_devolucao(bruto) is None  # nem devolução
    c = amazon_email.interpretar_confirmacao(bruto, uid=40)
    assert c is not None
    assert c.comprador_nome == "Maria Silva"
    assert c.texto == "Olá, Maria!\nTem cadeado TSA e cabe, sim."
    assert c.tag == "kfa"
    assert c.message_id == "<conf1@amazon.com>"
    assert c.enviada_em == datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
    assert c.pedido is None
    assert c.uid == 40


def test_copia_nome_pelo_corpo_e_o_que_nao_e_copia():
    # Assunto diferente, mas o cabeçalho diz que é cópia: nome vem do corpo.
    c = amazon_email.interpretar_confirmacao(_confirmacao(assunto="Cópia da sua mensagem"))
    assert c is not None and c.comprador_nome == "Maria Silva"
    # Sem o cabeçalho, o assunto "Seu e-mail para" basta.
    assert amazon_email.interpretar_confirmacao(_confirmacao(tipo=None)) is not None
    # Cabeçalho de OUTRO tipo manda: é outro aviso.
    outro = _confirmacao(tipo="BBC_MESSAGE_SENT_TO_MERCHANT")
    assert amazon_email.interpretar_confirmacao(outro) is None
    # Aviso comum do donotreply (sem cabeçalho, outro assunto): ignorado.
    aviso = _confirmacao(tipo=None, assunto="Atualização da política de mensagens")
    assert amazon_email.interpretar_confirmacao(aviso) is None
    assert amazon_email.interpretar_email(aviso) is None
    # Remetente que não é da Amazon, ou DMARC reprovado: não é cópia.
    golpe = _confirmacao(de="Amazon <donotreply@amazon.com.golpe.com>")
    assert amazon_email.interpretar_confirmacao(golpe) is None
    reprovado = _confirmacao(
        autenticacao="mx.google.com; spf=fail smtp.mailfrom=x.com;"
        " dmarc=fail header.from=amazon.com"
    )
    assert amazon_email.interpretar_confirmacao(reprovado) is None
    aprovado = _confirmacao(
        autenticacao="mx.google.com; dkim=pass; dmarc=pass header.from=amazon.com"
    )
    assert amazon_email.interpretar_confirmacao(aprovado) is not None
    # Sem os marcadores: é cópia, mas sem texto (nada de gravar o modelo inteiro).
    sem_marca = EmailMessage()
    sem_marca["From"] = "donotreply@amazon.com"
    sem_marca["To"] = "atendimento+kfa@loja-teste.com.br"
    sem_marca["Subject"] = "Seu e-mail para Maria Silva"
    sem_marca["Message-ID"] = "<conf9@amazon.com>"
    sem_marca.set_content("<p>Prezado, segue a cópia.</p>", subtype="html")
    c = amazon_email.interpretar_confirmacao(sem_marca.as_bytes())
    assert c is not None and c.texto is None


def _data_rel(agora: datetime, **delta) -> str:
    return format_datetime(agora - timedelta(**delta))


async def test_copia_vira_resposta_externa_sai_da_fila_e_cala_a_ia(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch, CaixaFalsa({30: _email_real(data=_data_rel(agora, hours=2))})
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    [conversa] = await _conversas(db)
    assert conversa.aguardando_resposta is True
    assert conversa.dados["amazon_link_sem_resposta"] == LINK_SEM_RESPOSTA
    assert conversa.dados["amazon_caso_id"] == CASO
    assert conversa.dados["amazon_link_caso"] == LINK_CASO
    [pergunta] = await _mensagens(db, conversa.id)
    assert pergunta.texto == "Olá, a mala tem cadeado?\n\nE cabe no bagageiro do avião?"

    # A IA já tinha deixado uma sugestão para a pergunta.
    rascunho = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=pergunta.id, texto="Sugestão da IA"
    )
    db.add(rascunho)
    await db.commit()

    # A pessoa respondeu pelo Seller Central; na mesma leva, um aviso qualquer
    # do donotreply (continua ignorado). Nome com caixa/acento diferentes.
    aviso = _confirmacao(
        tipo=None, assunto="Atualização da política de mensagens", message_id="<aviso@amazon.com>"
    )
    copia = _confirmacao(nome="MARIA SÍLVA", data=_data_rel(agora, hours=1))
    r = await _rodada(db, canal, kfa, redis_falso, caixa, (31, aviso), (32, copia))

    assert (r.mensagens_novas, r.conversas_novas, r.conversas_atualizadas) == (1, 0, 1)
    [conversa] = await _conversas(db)  # cópia nunca cria conversa
    await db.refresh(conversa)
    pergunta, resposta = await _mensagens(db, conversa.id)
    assert (resposta.autor, resposta.origem, resposta.status) == ("loja", "externo", "enviada")
    assert resposta.externo_id == "<conf1@amazon.com>"
    assert resposta.texto == "Olá, Maria!\nTem cadeado TSA e cabe, sim."
    assert resposta.enviada_em == agora - timedelta(hours=1)  # o Date da cópia
    assert resposta.payload == {"imap_uid": 32, "amazon_confirmacao": True}
    # Saiu da fila e a IA se cala: sugestão aposentada.
    assert (conversa.aguardando_resposta, conversa.prazo_resposta_em) == (False, None)
    assert conversa.situacao == "respondida"
    await db.refresh(rascunho)
    assert rascunho.status == "substituido"

    # Caixa migrada: tudo relido — nada duplica (Message-ID).
    caixa.uidvalidity = 999
    r = await _rodada(db, canal, kfa, redis_falso, caixa)
    assert r.mensagens_novas == 0
    assert len(await _mensagens(db, conversa.id)) == 2


async def test_copia_escolhe_a_conversa_certa_ou_so_loga(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """SEG-01/LOGICA-2: duas conversas do mesmo nome é empate — nenhuma fecha.

    Escolher "a mais recente" tirava da fila a pergunta que ninguém
    respondeu (a equipe responde primeiro a mais urgente, que é a mais
    VELHA) e deixava a respondida aguardando. Com uma candidata só, fecha.
    """
    agora = datetime.now(UTC).replace(microsecond=0)

    def _pergunta(uid_relay: str, nome: str, horas: float, conta: str, mid: str) -> bytes:
        return _email(
            de=f'"{nome}" <{uid_relay}@marketplace.amazon.com.br>',
            para=f"atendimento+{conta}@loja-teste.com.br",
            assunto="Pergunta do cliente",
            data=_data_rel(agora, hours=horas),
            message_id=mid,
            texto="Tem outra cor?",
            marcadores=False,  # o modelo do `_email` cita o pedido do KFA
        )

    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _pergunta("maria-velha", "Maria Silva", 20, "kfa", "<a@a>"),
                11: _pergunta("maria-nova", "Maria Silva", 5, "kfa", "<b@a>"),
                12: _pergunta("maria-kia", "Maria Silva", 2, "kia", "<c@a>"),
                13: _pergunta("joao", "João Souza", 1, "kfa", "<d@a>"),
                14: _pergunta("ana", "Ana Lima", 9 * 24, "kfa", "<e@a>"),
                15: _pergunta("carla", "Carla Dias", 4, "kfa", "<f@a>"),
            }
        ),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await _conta(db, user, "kia")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    por_relay = {c.comprador_id.split("@")[0]: c for c in await _conversas(db)}
    assert len(por_relay) == 6 and all(c.aguardando_resposta for c in por_relay.values())

    r = await _rodada(
        db,
        canal,
        kfa,
        redis_falso,
        caixa,
        # Duas "Maria Silva" aguardando no kfa → empate: nenhuma fecha, as
        # duas ganham o aviso; a do kia (outra conta) nem entra.
        (20, _confirmacao(message_id="<c1@amazon.com>", data=_data_rel(agora, minutes=30))),
        # A cópia é ANTERIOR à pergunta do João: é de outra resposta, não fecha.
        (21, _confirmacao(nome="João Souza", message_id="<c2@amazon.com>",
                          data=_data_rel(agora, hours=3))),
        # A pergunta da Ana tem 9 dias: fora da janela de 7. Datas relativas a
        # agora: com a data fixa do modelo, o teste passava a falhar dias depois.
        (22, _confirmacao(nome="Ana Lima", message_id="<c3@amazon.com>",
                          data=_data_rel(agora, minutes=10))),
        # Ninguém com esse nome; e uma sem "+conta": só log.
        (23, _confirmacao(nome="Pedro Alves", message_id="<c4@amazon.com>",
                          data=_data_rel(agora, minutes=10))),
        (24, _confirmacao(para=CAIXA, message_id="<c5@amazon.com>",
                          data=_data_rel(agora, minutes=10))),
        # Uma "Carla Dias" só: é ela.
        (25, _confirmacao(nome="Carla Dias", message_id="<c6@amazon.com>",
                          data=_data_rel(agora, minutes=20))),
    )

    assert r.mensagens_novas == 1
    for c in por_relay.values():
        await db.refresh(c)
    assert por_relay["carla"].aguardando_resposta is False
    for aguardando in ("maria-velha", "maria-nova", "maria-kia", "joao", "ana"):
        assert por_relay[aguardando].aguardando_resposta is True, aguardando
    assert len(await _conversas(db)) == 6  # nenhuma conversa nova
    externas = (
        await db.execute(select(AtendimentoMensagem).where(AtendimentoMensagem.origem == "externo"))
    ).scalars().all()
    assert [(m.conversa_id, m.externo_id) for m in externas] == [
        (por_relay["carla"].id, "<c6@amazon.com>")
    ]
    # O empate deixa aviso nas duas Marias do kfa (a tela mostra "conferir").
    for nome in ("maria-velha", "maria-nova"):
        aviso = por_relay[nome].dados["amazon_copia_a_conferir"]
        assert aviso["message_id"] == "<c1@amazon.com>", nome
    assert "amazon_copia_a_conferir" not in (por_relay["maria-kia"].dados or {})
    # A do João espera a pergunta que ela responde (pode ser aviso atrasado).
    [cursor, *_] = await _cursores(db)
    assert "<c2@amazon.com>" in [c["message_id"] for c in cursor["copias_pendentes"]]


async def test_copia_com_pedido_prefere_a_conversa_do_pedido(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(data=_data_rel(agora, hours=6), message_id="<p1@a>"),  # pedido KFA
                11: _email(
                    de=f'"Maria Silva" <{RELAY_JOAO}>',
                    assunto="Pergunta sobre produto",
                    data=_data_rel(agora, hours=2),
                    message_id="<p2@a>",
                    texto="Tem outra cor?",
                    marcadores=False,  # o modelo do `_email` cita o pedido do KFA
                ),
            }
        ),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    copia = _confirmacao(
        resposta=f"Seu pedido {PEDIDO_KFA} sai hoje.", data=_data_rel(agora, hours=1)
    )
    await _rodada(db, canal, kfa, redis_falso, caixa, (20, copia))
    por_pedido = {c.pedido_marketplace: c for c in await _conversas(db)}
    for c in por_pedido.values():
        await db.refresh(c)
    assert por_pedido[PEDIDO_KFA].aguardando_resposta is False  # a do pedido, não a mais nova
    assert por_pedido[None].aguardando_resposta is True


async def test_copia_da_resposta_que_saiu_pelo_davinci_nao_duplica(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """Se a Amazon mandar cópia do que saiu PELO DaVinci, a conversa já está
    respondida (não aguarda): a cópia não vira uma segunda resposta."""
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa({10: _email(de=f'"Maria Silva" <{RELAY_MARIA}>',
                               data=_data_rel(agora, hours=3))}),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    [conversa] = await _conversas(db)
    await _nossa_resposta(
        db, conversa, "<nossa@loja-teste.com.br>", enviada_em=agora - timedelta(hours=2)
    )

    copia = _confirmacao(resposta="Seu pedido chega amanhã.", data=_data_rel(agora, hours=2))
    r = await _rodada(db, canal, kfa, redis_falso, caixa, (20, copia))
    assert r.mensagens_novas == 0
    assert len(await _mensagens(db, conversa.id)) == 2


async def test_links_do_rodape_seguem_o_ultimo_email_e_o_caso_fica(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch, CaixaFalsa({30: _email_real(data=_data_rel(agora, hours=3))})
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    # O seguinte, da mesma thread, sem rodapé: o "não precisa de resposta"
    # velho dispensaria a pergunta errada — some; o caso continua.
    seguinte = _email_real(
        data=_data_rel(agora, hours=1),
        message_id="<real2@amazon.com.br>",
        msg1="Alguém?",
        msg2="Preciso até sexta.",
        com_links=False,
        de='"Maria Silva" <anonimo42@marketplace.amazon.com.br>',
        em_resposta_a="<real1@amazon.com.br>",
    )
    await _rodada(db, canal, kfa, redis_falso, caixa, (31, seguinte))
    [conversa] = await _conversas(db)
    await db.refresh(conversa)
    assert conversa.dados["ultimo_message_id"] == "<real2@amazon.com.br>"
    assert conversa.dados["amazon_link_sem_resposta"] is None
    assert conversa.dados["amazon_caso_id"] == CASO
    assert conversa.dados["amazon_link_caso"] == LINK_CASO
    textos = [m.texto for m in await _mensagens(db, conversa.id)]
    assert all("http" not in t for t in textos)


# ─────────────── revisão 4: cópia sem chute, links do rodapé, releitura ───────────────


async def test_copia_da_nossa_resposta_nao_fecha_outra_conversa_do_mesmo_comprador(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """SEG-02: Maria tem a pré-venda (sem pedido) e a pós-venda (pedido KFA).
    O DaVinci responde a pós-venda — o e-mail sai com "Pedido: 701-...". A
    cópia dessa resposta, se a Amazon mandar, cai no NOME e fechava a
    pré-venda, que ninguém respondeu. Agora ela é reconhecida como NOSSA."""
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(
                    de=f'"Maria Silva" <{RELAY_JOAO}>',
                    assunto="Pergunta sobre produto",
                    data=_data_rel(agora, hours=4),
                    message_id="<pre@a>",
                    texto="Tem outra cor?",
                    marcadores=False,
                ),
                11: _email(
                    de=f'"Maria Silva" <{RELAY_MARIA}>',
                    data=_data_rel(agora, hours=3),
                    message_id="<pos@a>",
                ),
            }
        ),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    por_pedido = {c.pedido_marketplace: c for c in await _conversas(db)}
    assert set(por_pedido) == {None, PEDIDO_KFA}
    nossa = await _nossa_resposta(
        db,
        por_pedido[PEDIDO_KFA],
        "<nossa@loja-teste.com.br>",
        enviada_em=agora - timedelta(hours=1, minutes=5),
    )
    # O texto que SAIU: o digitado + o "Pedido:" que o `montar_resposta` põe.
    copia = _confirmacao(
        resposta=amazon_email.corpo_da_resposta(nossa.texto, PEDIDO_KFA),
        data=_data_rel(agora, hours=1),
    )
    r = await _rodada(db, canal, kfa, redis_falso, caixa, (20, copia))

    assert r.mensagens_novas == 0
    for c in por_pedido.values():
        await db.refresh(c)
    assert por_pedido[None].aguardando_resposta is True  # a pré-venda continua na fila
    externas = (
        await db.execute(select(AtendimentoMensagem).where(AtendimentoMensagem.origem == "externo"))
    ).scalars().all()
    assert externas == []
    await db.refresh(nossa)
    assert nossa.payload["amazon_confirmacao_mid"] == "<conf1@amazon.com>"  # confirma o envio


async def test_copia_que_cita_pedido_sem_conversa_nao_cai_no_nome(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """SEG-02 (a): a cópia cita um pedido que nenhuma conversa tem → é de outro
    assunto; cair no nome fechava a pré-venda do mesmo comprador."""
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(
                    de=f'"Maria Silva" <{RELAY_JOAO}>',
                    assunto="Pergunta sobre produto",
                    data=_data_rel(agora, hours=4),
                    message_id="<pre@a>",
                    texto="Tem outra cor?",
                    marcadores=False,
                )
            }
        ),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    [pre_venda] = await _conversas(db)

    copia = _confirmacao(
        resposta=f"O seu pedido {PEDIDO_KIA} já foi postado.", data=_data_rel(agora, hours=1)
    )
    r = await _rodada(db, canal, kfa, redis_falso, caixa, (20, copia))
    assert r.mensagens_novas == 0
    await db.refresh(pre_venda)
    assert pre_venda.aguardando_resposta is True
    assert len(await _mensagens(db, pre_venda.id)) == 1


async def test_duas_copias_seguidas_vao_as_duas_para_o_historico(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """LOGICA-3 (a): a 1ª cópia fecha a conversa; a 2ª (a loja mandou duas
    mensagens) sumia, porque só conversa AGUARDANDO era candidata."""
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch, CaixaFalsa({30: _email_real(data=_data_rel(agora, hours=2))})
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    c1 = _confirmacao(
        resposta="Tem cadeado sim.", data=_data_rel(agora, minutes=50), message_id="<c1@x>"
    )
    c2 = _confirmacao(
        resposta="E cabe no bagageiro.", data=_data_rel(agora, minutes=49), message_id="<c2@x>"
    )
    r = await _rodada(db, canal, kfa, redis_falso, caixa, (31, c1), (32, c2))
    assert r.mensagens_novas == 2
    [conversa] = await _conversas(db)
    await db.refresh(conversa)
    textos = [m.texto for m in await _mensagens(db, conversa.id)]
    assert textos[1:] == ["Tem cadeado sim.", "E cabe no bagageiro."]
    assert conversa.aguardando_resposta is False


async def test_copia_mais_velha_que_a_pergunta_nova_grava_sem_tirar_da_fila(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """LOGICA-3 (b): o comprador escreveu de novo antes de a cópia ser lida
    (mesma rodada). A cópia era descartada — a resposta real sumia do
    histórico que a IA lê. Agora entra no lugar dela (pelo Date) e a
    conversa continua na fila pela pergunta nova."""
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch, CaixaFalsa({30: _email_real(data=_data_rel(agora, hours=3))})
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()

    nova = _email_real(
        data=_data_rel(agora, minutes=30),
        message_id="<real2@amazon.com.br>",
        msg1="ok, e tem azul?",
        msg2="obrigada",
        em_resposta_a="<real1@amazon.com.br>",
    )
    copia = _confirmacao(resposta="Tem cadeado sim.", data=_data_rel(agora, hours=1))
    r = await _rodada(db, canal, kfa, redis_falso, caixa, (31, nova), (32, copia))
    assert r.mensagens_novas == 2
    [conversa] = await _conversas(db)
    await db.refresh(conversa)
    autores = [(m.autor, m.origem) for m in await _mensagens(db, conversa.id)]
    assert autores == [("cliente", "cliente"), ("loja", "externo"), ("cliente", "cliente")]
    assert conversa.aguardando_resposta is True  # a pergunta nova ainda espera


async def test_copia_que_chega_antes_da_pergunta_espera_e_grava_quando_ela_chega(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """LOGICA-3 (c): o aviso do comprador atrasou uma rodada. A cópia era
    consumida sem conversa e a pergunta ficava aguardando para sempre."""
    agora = datetime.now(UTC).replace(microsecond=0)
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa({30: _confirmacao(resposta="Tem sim.", data=_data_rel(agora, hours=1))}),
    )
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    r = await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    assert r.mensagens_novas == 0 and await _conversas(db) == []
    [cursor] = await _cursores(db)
    assert [c["message_id"] for c in cursor["copias_pendentes"]] == ["<conf1@amazon.com>"]

    # Rodada sem pergunta nova: continua guardada (nem se tenta).
    r = await _rodada(db, canal, kfa, redis_falso, caixa)
    [cursor] = await _cursores(db)
    assert len(cursor["copias_pendentes"]) == 1

    # A pergunta (anterior à cópia) chega: a cópia entra e fecha a conversa.
    r = await _rodada(
        db, canal, kfa, redis_falso, caixa, (31, _email_real(data=_data_rel(agora, hours=2)))
    )
    assert r.mensagens_novas == 2
    [conversa] = await _conversas(db)
    await db.refresh(conversa)
    pergunta, resposta = await _mensagens(db, conversa.id)
    assert (resposta.origem, resposta.texto) == ("externo", "Tem sim.")
    assert conversa.aguardando_resposta is False
    [cursor] = await _cursores(db)
    assert "copias_pendentes" not in cursor


def test_copias_pendentes_no_cursor_vencem_e_tem_teto():
    agora = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def _c(mid: str, horas: float) -> amazon_email.ConfirmacaoAmazon:
        return amazon_email.ConfirmacaoAmazon(
            message_id=mid, tag="kfa", comprador_nome="Maria", texto="Oi",
            pedido=None, enviada_em=agora - timedelta(hours=horas), uid=1,
        )

    velha, nova = _c("<v@x>", 25), _c("<n@x>", 1)
    assert amazon_email._pendentes_em_dia([velha, nova, nova], agora) == [nova]
    muitas = [_c(f"<{i}@x>", i / 100) for i in range(amazon_email.MAX_COPIAS_PENDENTES + 5)]
    ficaram = amazon_email._pendentes_em_dia(muitas, agora)
    assert len(ficaram) == amazon_email.MAX_COPIAS_PENDENTES
    assert "<0@x>" in {c.message_id for c in ficaram}  # as mais novas ficam
    # Ida e volta pelo JSON; item torto some.
    volta = amazon_email._copia_do_cursor(amazon_email._copia_para_cursor(nova))
    assert volta == nova
    for torto in (None, "x", {"message_id": "<a@x>"}, {"message_id": "<a@x>", "em": "ontem"}):
        assert amazon_email._copia_do_cursor(torto) is None


async def test_reler_caixa_nao_duplica_email_sem_pedido(
    db: AsyncSession, make_user, caixa_configurada, redis_falso, monkeypatch
):
    """LOGICA-6: E1 (sem pedido) e depois E2 (com pedido) do mesmo endereço.
    Na releitura (UIDVALIDITY nova), E1 ia para "a conversa mais recente do
    endereço" — a do E2 — e era gravado de novo lá."""
    agora = datetime.now(UTC).replace(microsecond=0)
    relay = "anon77@marketplace.amazon.com.br"
    e1 = _email(de=f'"Maria" <{relay}>', assunto="Pergunta", data=_data_rel(agora, hours=48),
                message_id="<e1@a>", texto="Tem azul?", marcadores=False)
    e2 = _email(de=f'"Maria" <{relay}>', assunto=f"Pedido {PEDIDO_KFA}",
                data=_data_rel(agora, hours=2), message_id="<e2@a>",
                texto="Comprei, quando sai?", marcadores=False)
    caixa = _instalar_caixa(monkeypatch, CaixaFalsa({10: e1, 11: e2}))
    user = await make_user()
    kfa, canal = await _conta(db, user, "kfa")
    await amazon_email.sincronizar(db, canal, kfa, None)
    await db.commit()
    assert len(await _conversas(db)) == 2

    caixa.uidvalidity = 999  # caixa migrada: relê os 7 dias
    r = await _rodada(db, canal, kfa, redis_falso, caixa)
    assert r.mensagens_novas == 0
    todas = (await db.execute(select(AtendimentoMensagem.externo_id))).scalars().all()
    assert sorted(todas) == ["<e1@a>", "<e2@a>"]


def test_link_do_seller_central_escrito_pelo_comprador_nao_vira_botao():
    """SEG-04: o comprador cola, na mensagem (antes do rodapé), um "não precisa
    de resposta" com assinatura falsa e o link de OUTRO caso. O botão da tela
    tem de continuar sendo o do rodapé da Amazon."""
    falso = (
        "https://sellercentral.amazon.com.br/messaging/no-response-needed?t=X&m=Y"
        f"&mp=A2Q3Y263D00KWC&c=A2C4PS5Z87WJEW&h={'1' * 40}&s=1"
    )
    outro_caso = (
        "https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId"
        "&cc=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
    em = amazon_email.interpretar_email(_email_real(msg1=f"Veja {falso}", msg2=f"e {outro_caso}"))
    assert em.link_sem_resposta == LINK_SEM_RESPOSTA
    assert em.caso_id == CASO
    assert "http" not in em.texto

    # Relay sem "+caso": o caso vem do rodapé, nunca do link do comprador.
    sem_mais = amazon_email.interpretar_email(
        _email_real(de='"Maria" <anonimo42@marketplace.amazon.com.br>', msg2=f"e {outro_caso}")
    )
    assert sem_mais.caso_id == CASO
    # Sem o marcador de fim (formato desconhecido): vale a ÚLTIMA ocorrência.
    solto = amazon_email._urls_do_rodape(
        f"Oi {falso} tudo bem\n\nSolucionar o caso {LINK_SEM_RESPOSTA}\n", None
    )
    assert solto[0] == LINK_SEM_RESPOSTA
    # O caso pelo "+" do From vale mais que o do link.
    _sem, caso = amazon_email._links_do_rodape(
        f"{_T} Encerrar mensagem {_T}\n{outro_caso}", None, RELAY_CASO
    )
    assert caso == CASO


def test_copia_de_uma_linha_com_fim_desconhecido_nao_grava_o_rodape():
    """LOGICA-5: a Amazon troca o marcador de fim (já trocou em 28/09) e a
    cópia vem num parágrafo só: o texto gravado levava o rodapé inteiro."""
    html = (
        "<div>Prezado Loja Teste, Aqui está uma cópia do e-mail que você enviou para "
        f"Maria Silva. {_T} Iniciar mensagem {_T} Olá! Tem sim. {_T} Fim do texto {_T} "
        "Atenciosamente, Amazon.com.br http://www.amazon.com.br "
        f"{_RODAPE_AMAZON} Este e-mail foi útil? [commMgrTok:ATESTE0000000000003]</div>"
    )
    bruto = EmailMessage()
    bruto["From"] = '"Amazon.com.br" <donotreply@amazon.com>'
    bruto["To"] = "atendimento+kfa@loja-teste.com.br"
    bruto["Subject"] = "Seu e-mail para Maria Silva"
    bruto["Message-ID"] = "<conf7@amazon.com>"
    bruto.set_content(html, subtype="html")
    c = amazon_email.interpretar_confirmacao(bruto.as_bytes())
    assert c is not None and c.texto == "Olá! Tem sim."

    # Sem marcador de traços, o rodapé solto no meio da linha também corta.
    sem_tracos = html.replace(f"{_T} Fim do texto {_T} ", "")
    bruto.set_content(sem_tracos, subtype="html")
    c = amazon_email.interpretar_confirmacao(bruto.as_bytes())
    assert c is not None and c.texto == "Olá! Tem sim."
    # O e-mail do comprador com o fim conhecido não muda: traço no texto fica.
    corpo = (
        f"{_T} Mensagem:  {_T}\nOi\n-----\nPara o setor\n-----\nobg\n"
        f"{_T} Encerrar mensagem {_T}"
    )
    assert amazon_email.extrair_mensagem(corpo) == "Oi\n-----\nPara o setor\n-----\nobg"
