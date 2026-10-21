"""A Entrada (INBOX), os Enviados e a caixa da Goslin ligam EXATAMENTE como antes (21/10/2026).

Decisão do dono (21/10, "a entrada pode ligar ainda, não?"): a categoria por
pasta (Venda · Chamado · Informativo) vale SÓ para as pastas criadas; a
Entrada continua ligando como sempre (loja pelo alias/remetente/caixa de site,
tipo pelo conteúdo), os Enviados também, e a caixa da Goslin (só Entrada) não
muda nada.

Como se prova "exatamente": o `INSTANTANEO` (tests/fixtures/mail_entrada_igual.json)
foi gravado com o código de ANTES da categoria por pasta (27ff9162, wip/agente-
fatos) rodando os mesmos e-mails abaixo; aqui cada coluna que já existia na
meta, na conversa e na mensagem tem de sair IGUAL (ids, horas, hashes e ids do
Tuta trocados por fichas na ordem em que aparecem), e cada coluna NOVA tem de
ficar vazia (a Entrada nunca ganha categoria nem chamado).

Gravar de novo (só se a regra da Entrada mudar DE PROPÓSITO):
MAIL_ENTRADA_GRAVAR=1 pytest tests/test_mail_entrada_igual.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, inspect, select

from app.models import AtendimentoConversa, AtendimentoMensagem, MailMessage
from app.models.mail_atendimento import MailFolder, MailMessageMeta
from tests.test_mail_ponte import (
    GOSLIN,
    _config,  # noqa: F401 — o ambiente da ponte
    cena,  # noqa: F401 — o mundo da ponte (as duas caixas, as lojas, a marca)
    entrar,
    pedido_existe,
    rodar,
)

INSTANTANEO = Path(__file__).parent / "fixtures" / "mail_entrada_igual.json"
# As colunas que o "antes" não tinha: na Entrada ficam sempre vazias.
_NOVAS = {
    "mail_message_meta": {"chamado_id"},
    "mail_folders": {"categoria", "categoria_por", "categoria_em"},
}
_FORA = {"created_at", "updated_at"}
_RE_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_RE_HASH = re.compile(r"\b[0-9a-f]{64}\b")
_RE_TUTA = re.compile(r"\bL\d+/E\d+\b")
_RE_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")


class _Fichas:
    """Troca o que muda a cada rodada (ids, horas, hashes, ids do Tuta) por fichas estáveis."""

    def __init__(self) -> None:
        self.mapa: dict[str, str] = {}

    def _ficha(self, prefixo: str, valor: str) -> str:
        chave = f"{prefixo}:{valor}"
        if chave not in self.mapa:
            n = sum(k.startswith(prefixo + ":") for k in self.mapa) + 1
            self.mapa[chave] = f"<{prefixo}{n}>"
        return self.mapa[chave]

    def texto(self, s: str) -> str:
        s = _RE_UUID.sub(lambda m: self._ficha("id", m.group(0)), s)
        s = _RE_HASH.sub(lambda m: self._ficha("hash", m.group(0)), s)
        s = _RE_TUTA.sub(lambda m: self._ficha("tuta", m.group(0)), s)
        return _RE_ISO.sub("<hora>", s)

    def valor(self, v):
        if isinstance(v, UUID):
            return self._ficha("id", str(v))
        if isinstance(v, datetime | date):
            return "<hora>"
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, str):
            return self.texto(v)
        if isinstance(v, dict):
            return {k: self.valor(x) for k, x in sorted(v.items())}
        if isinstance(v, list | tuple):
            return [self.valor(x) for x in v]
        return v


def _colunas(obj) -> dict:
    saida = {
        a.key: getattr(obj, a.key) for a in inspect(obj).mapper.column_attrs if a.key not in _FORA
    }
    if isinstance(saida.get("sugestoes"), list):
        # A ordem das sugestões já vinha da ordem dos ids no cadastro (muda a cada
        # rodada, antes e depois): compara o CONJUNTO.
        saida["sugestoes"] = sorted(
            saida["sugestoes"],
            key=lambda s: (str(s.get("plataforma")), str(s.get("nome")), str(s.get("marca_slug"))),
        )
    return saida


async def _cenario(db, cena) -> list:  # noqa: F811 — `cena` é a fixture importada
    """Os e-mails da Entrada/Enviados, um por vez (a ordem importa: fio, duplicado)."""
    await pedido_existe(db, cena.oliveira_ml, "2000012345670001")
    await pedido_existe(db, cena.ml, "2000012345670002")
    await pedido_existe(db, cena.ml, "2000012345670003")
    roteiro = [
        # Goslin (IMAP, só a Entrada): o cliente no alias de duas plataformas.
        {
            "caixa": cena.goslin,
            "pasta": "INBOX",
            "para": ["mia30@tuta.com"],
            "message_id": "<e1@c.com>",
        },
        # Goslin: o aviso oficial do ML com o pedido que existe.
        {
            "caixa": cena.goslin,
            "pasta": "INBOX",
            "para": ["mia30@tuta.com"],
            "de": "aviso@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Nova mensagem no pedido 2000012345670001",
            "corpo": "O comprador mandou uma mensagem.",
            "message_id": "<e2@ml.com>",
        },
        # Goslin: o pessoal do dono (privado, nada em claro).
        {"caixa": cena.goslin, "pasta": "INBOX", "para": [GOSLIN], "message_id": "<e3@c.com>"},
        # Geral: o caso rotulado do ML (aviso oficial) na Entrada.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "aviso@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Reclamação 5012345678 aberta no pedido 2000012345670002",
            "corpo": "Caso 5012345678: o comprador abriu uma reclamação.",
            "message_id": "<e4@ml.com>",
        },
        # Geral: o cliente respondendo no fio do caso (cliente que a equipe não conhece).
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "assunto": "Re: Reclamação",
            "corpo": "Ainda não chegou.",
            "in_reply_to": "<e4@ml.com>",
            "references": ["<e4@ml.com>"],
            "message_id": "<e5@c.com>",
        },
        # Geral: o cliente na caixa de SITE (sac@ da marca) com protocolo.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "para": ["sac@uranyx.com.br"],
            "assunto": "[US-26-0031] Troca",
            "corpo": "Quero trocar a capa.",
            "message_id": "<e6@c.com>",
        },
        # Geral: o FORMULÁRIO do site (DE sac@ PARA sac@), outro protocolo.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "sac@uranyx.com.br",
            "de_nome": "Site Uranyx",
            "para": ["sac@uranyx.com.br"],
            "assunto": "[US-26-0032] Defeito",
            "corpo": (
                "Nome: Ana Souza\nE-mail: ana.souza@gmail.com\nTelefone: (11) 98888-7777\n"
                "Pedido: 4455\nMensagem: a tela apagou"
            ),
            "message_id": "<e7@site.com>",
        },
        # Geral: código de verificação → segurança.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "no-reply@mercadolivre.com.br",
            "assunto": "Seu código de verificação",
            "corpo": "Use 482913 para entrar na sua conta.",
            "message_id": "<e8@ml.com>",
        },
        # Geral: só para endereço interno → ignorado.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "para": ["adm@poofy.com.br"],
            "message_id": "<e9@c.com>",
        },
        # Geral: novidades do ML (informativo pelo conteúdo) na Entrada.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "novidades@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Novidades da semana para vendedores",
            "corpo": "Confira as novidades.",
            "message_id": "<e10@ml.com>",
        },
        # Geral: cliente da Amazon (mike14 é só Amazon).
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "para": ["mike14@tuta.com"],
            "assunto": "Dúvida",
            "corpo": "O produto serve no modelo X?",
            "message_id": "<e11@c.com>",
        },
        # Geral: a ficha sem integração (16tr) com dois pedidos que não existem.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "para": ["16tr@tuta.com"],
            "de": "aviso@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Pedidos 2000099999990001 e 2000099999990002",
            "corpo": "Dois pedidos.",
            "message_id": "<e12@ml.com>",
        },
        # Geral: dois pedidos que existem → "varios_pedidos".
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "aviso@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Pedidos 2000012345670002 e 2000012345670003",
            "corpo": "Dois pedidos da loja.",
            "message_id": "<e13@ml.com>",
        },
        # Goslin: a cópia do e-mail 2 (mesmo Message-ID) → duplicado.
        {
            "caixa": cena.goslin,
            "pasta": "INBOX",
            "para": ["mia30@tuta.com"],
            "de": "aviso@mercadolivre.com.br",
            "de_nome": "Mercado Livre",
            "assunto": "Nova mensagem no pedido 2000012345670001",
            "corpo": "O comprador mandou uma mensagem.",
            "message_id": "<e2@ml.com>",
        },
        # Geral: o golpe que imita a plataforma, citando o pedido.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "suporte@mercadolivre-br.com",
            "de_nome": "Mercado Livre",
            "assunto": "Pedido 2000012345670002 bloqueado",
            "corpo": "Clique para liberar.",
            "message_id": "<e15@golpe.com>",
        },
        # Geral: a resposta da cliente do site no fio do e-mail 6.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "para": ["sac@uranyx.com.br"],
            "assunto": "Re: [US-26-0031] Troca",
            "corpo": "Alguma novidade?",
            "in_reply_to": "<e6@c.com>",
            "references": ["<e6@c.com>"],
            "message_id": "<e16@c.com>",
        },
        # Geral: a devolução do servidor da cliente no fio do e-mail 6.
        {
            "caixa": cena.geral,
            "pasta": "INBOX",
            "de": "mailer-daemon@gmail.com",
            "de_nome": "Mail Delivery Subsystem",
            "para": ["sac@uranyx.com.br"],
            "assunto": "Delivery Status Notification (Failure)",
            "corpo": "Address not found.",
            "in_reply_to": "<e6@c.com>",
            "references": ["<e6@c.com>"],
            "message_id": "<e17@gmail.com>",
        },
        # Geral: a resposta dada direto no Tuta (Enviados) no fio do e-mail 6.
        {
            "caixa": cena.geral,
            "pasta": "Sent",
            "de": "sac@uranyx.com.br",
            "para": ["maria@gmail.com"],
            "direcao": "sent",
            "assunto": "Re: [US-26-0031] Troca",
            "corpo": "Já enviamos a troca.",
            "in_reply_to": "<e6@c.com>",
            "references": ["<e6@c.com>"],
            "message_id": "<e18@uranyx.com.br>",
        },
        # Geral: um enviado sem conversa nenhuma.
        {
            "caixa": cena.geral,
            "pasta": "Sent",
            "de": "21max@tuta.com",
            "para": ["joao@gmail.com"],
            "direcao": "sent",
            "assunto": "Oi",
            "corpo": "Mensagem avulsa.",
            "message_id": "<e19@tuta.com>",
        },
    ]
    resumos = []
    ids = []
    for item in roteiro:
        caixa = item.pop("caixa")
        ids.append(await entrar(db, caixa, **item))
        resumos.append(await rodar(db))
    return ids, resumos


async def _foto(db, ids: list[UUID], resumos: list[dict]) -> dict:
    """Tudo o que a ponte gravou para estes e-mails, com as fichas no lugar do que muda."""
    f = _Fichas()
    db.expire_all()
    metas, conversas, msgs, pastas = [], {}, {}, {}
    for mid in ids:
        meta = await db.get(MailMessageMeta, mid)
        metas.append(_colunas(meta) if meta is not None else None)
        if meta is None:
            continue
        if meta.folder_id is not None and meta.folder_id not in pastas:
            pastas[meta.folder_id] = _colunas(await db.get(MailFolder, meta.folder_id))
        if meta.conversa_id is not None and meta.conversa_id not in conversas:
            conversas[meta.conversa_id] = _colunas(
                await db.get(AtendimentoConversa, meta.conversa_id)
            )
            msgs[meta.conversa_id] = [
                _colunas(m)
                for m in (
                    await db.execute(
                        select(AtendimentoMensagem)
                        .where(AtendimentoMensagem.conversa_id == meta.conversa_id)
                        .order_by(AtendimentoMensagem.created_at, AtendimentoMensagem.enviada_em)
                    )
                ).scalars()
            ]
    bruto = {
        "metas": metas,
        "pastas": list(pastas.values()),
        "conversas": list(conversas.values()),
        "mensagens": list(msgs.values()),
        "resumos": resumos,
        "totais": {
            nome: int(await db.scalar(select(func.count()).select_from(modelo)))
            for nome, modelo in (
                ("conversas", AtendimentoConversa),
                ("mensagens", AtendimentoMensagem),
                ("emails", MailMessage),
            )
        },
    }
    return f.valor(bruto)


def _sem_novas(tabela: str, linhas: list) -> list:
    """As colunas novas saem da comparação, mas TÊM de estar vazias."""
    saida = []
    for linha in linhas:
        if linha is None:
            saida.append(None)
            continue
        for coluna in _NOVAS.get(tabela, ()):
            assert linha.get(coluna) in (None, False), (tabela, coluna, linha.get(coluna))
        saida.append({k: v for k, v in linha.items() if k not in _NOVAS.get(tabela, ())})
    return saida


async def test_entrada_enviados_e_goslin_ligam_exatamente_como_antes(db, cena):  # noqa: F811
    ids, resumos = await _cenario(db, cena)
    foto = await _foto(db, ids, resumos)
    foto["metas"] = _sem_novas("mail_message_meta", foto["metas"])
    foto["pastas"] = _sem_novas("mail_folders", foto["pastas"])
    if os.environ.get("MAIL_ENTRADA_GRAVAR") == "1":
        INSTANTANEO.write_text(json.dumps(foto, ensure_ascii=False, indent=1, sort_keys=True))
    esperado = json.loads(INSTANTANEO.read_text())
    atual = json.loads(json.dumps(foto, ensure_ascii=False, sort_keys=True))
    # Coluna a coluna, para o erro dizer ONDE mudou.
    for chave in ("resumos", "totais", "pastas", "metas", "conversas", "mensagens"):
        assert atual[chave] == esperado[chave], chave
    assert atual == esperado
    # Nada foi para a Pós-venda nem virou linha de informativo.
    estados = {m["estado"] for m in atual["metas"] if m}
    assert "informativo" not in estados
