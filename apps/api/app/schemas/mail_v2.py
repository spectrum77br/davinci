"""Contrato v2 do agente do Mac — o NOSSO conector do Tuta (08/10/2026).

O v1 da Central (`schemas/mail.py`) fica CONGELADO: caminhos, corpos,
respostas e limites. O v2 mora num caminho próprio
(`/api/mail/agent/{id}/v2/*`), com a MESMA chave por caixa, e carrega só o
que o v1 não carrega:

  • os ids do Tuta (o `source_id` é "tuta:<lista>/<elemento>" e o bloco
    `tuta` repete o id, a pasta com chave/caminho/tipo, o fio, os avisos de
    golpe do próprio Tuta — `phishing_status`, `auth_status`, o envelope);
  • `delivered_to` (o alias da conta que recebeu);
  • as pastas e os aliases ATIVOS da conta (`/sync`), e quais pastas o
    servidor manda ler com corpo (o conector obedece);
  • a contagem por pasta e o movido/apagado (`/count`, `/changes`).

Tudo herda o `StrictModel` da Central (campo a mais = 422) e os limites do v1
continuam valendo para o e-mail (assunto, endereços, texto, anexos). O texto
continua TEXTO (o conector converte o HTML do Tuta no Mac; os códigos e os
links de acesso já chegam mascarados).

Os nomes dos campos ficam em inglês, como o contrato v1 da Central.
"""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, EmailStr, Field, field_validator, model_validator

from app.schemas.mail import MessageIn, StrictModel

# O id de um MailSet/lista/elemento do Tuta (base64ext: letras, números, - e _).
TutaId = Annotated[str, Field(min_length=1, max_length=191, pattern=r"^[A-Za-z0-9_-]+$")]
# "lista/elemento" do Mail.
TutaPar = Annotated[
    str, Field(min_length=3, max_length=191, pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
]
# O source_id do v1 (sem caractere de controle), até 191.
SourceId = Annotated[str, Field(min_length=1, max_length=191, pattern=r"^[^\x00-\x1f]*$")]
# Texto curto de uma linha (nome/caminho de pasta, versão).
Linha = Annotated[str, Field(max_length=1000, pattern=r"^[^\x00-\x1f]*$")]
# Os números do Tuta que vêm em texto ("1" = Entrada, "0"…).
Numero = Annotated[str, Field(max_length=2, pattern=r"^[0-9]{1,2}$")]

MAX_CONTADORES = 40
MAX_PASTAS = 500
MAX_ALIASES_CONTA = 500
MAX_IDS_CONTAGEM = 5000
MAX_MUDANCAS = 200
# O que o conector deixou de mandar de um anexo (o e-mail vai sem ele).
MOTIVOS_ANEXO = Literal["perigoso", "grande_demais", "ilegivel", "teto_do_email"]


class FolderIn(StrictModel):
    """Uma pasta (MailSet) da conta: a chave não muda se a pessoa renomear."""

    key: TutaId
    name: Linha = ""
    path: Linha = ""
    # MailSetKind: 0 pessoal, 1 Entrada, 2 Enviados, 3 Lixeira, 4 Arquivo,
    # 5 Spam, 6 Rascunhos, 7 Todos, 8 marcador, 9 importados, 10 agendados.
    kind: int = Field(ge=0, le=99)
    parent: TutaId | None = None


class SyncIn(StrictModel):
    """O conector a cada volta: quem é, como está e (quando mudou) a conta."""

    # O id da instância, guardado no Mac POR CONTA (um reinício manda o mesmo).
    instance: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    agent_version: str = Field(default="", max_length=64, pattern=r"^[^\x00-\x1f]*$")
    tuta_version: str = Field(default="", max_length=32, pattern=r"^[^\x00-\x1f]*$")
    # Só números (lidos, ilegíveis, enviados pela conta na última hora…).
    counters: dict[str, int] = Field(default_factory=dict)
    # None = não mudou desde o último /sync (a lista inteira quando mudar).
    folders: list[FolderIn] | None = Field(default=None, max_length=MAX_PASTAS)
    # False = alguma pasta não decifrou agora: a lista não está inteira (as que
    # não vieram NÃO são marcadas como sumidas).
    folders_complete: bool = True
    aliases: list[EmailStr] | None = Field(default=None, max_length=MAX_ALIASES_CONTA)

    @field_validator("counters")
    @classmethod
    def so_numeros(cls, valor: dict[str, int]) -> dict[str, int]:
        if len(valor) > MAX_CONTADORES:
            raise ValueError("too_many_counters")
        for nome, numero in valor.items():
            if not (1 <= len(nome) <= 40) or not nome.replace("_", "").isalnum():
                raise ValueError("invalid_counter_name")
            if not nome.isascii() or nome.lower() != nome:
                raise ValueError("invalid_counter_name")
            if not (0 <= numero <= 1_000_000_000):
                raise ValueError("invalid_counter_value")
        return valor


class TutaIn(StrictModel):
    """O que o v1 não carrega de um e-mail do Tuta (fica cifrado com ele)."""

    mail_id: TutaPar
    folder_key: TutaId
    folder_kind: Numero
    folder_path: Linha = ""
    # O fio do Tuta (a lista do ConversationEntry).
    conversation_id: TutaId | None = None
    # Mail.state: 0 rascunho, 1 enviado, 2 recebido, 3 enviando.
    state: int | None = Field(default=None, ge=0, le=9)
    unread: bool | None = None
    # Mail.replyType: 0 nada, 1 respondido, 2 encaminhado, 3 os dois.
    replied: int | None = Field(default=None, ge=0, le=9)
    # Os avisos do PRÓPRIO Tuta (suspeito.py): phishing 1 = suspeito; auth 1-4 = falha.
    phishing_status: Numero | None = None
    auth_status: Numero | None = None
    # Mail.differentEnvelopeSender (só soma ao aviso de golpe).
    envelope_sender: str | None = Field(default=None, max_length=254, pattern=r"^[^\s\x00-\x1f]*$")
    labels: list[TutaId] = Field(default_factory=list, max_length=20)
    sent_at: AwareDatetime | None = None
    # O conector mascarou códigos / tirou links de acesso (D8).
    codes_masked: bool = False
    links_removed: int = Field(default=0, ge=0, le=100_000)


class OmittedAttachment(StrictModel):
    """Um anexo que o conector NÃO mandou (o e-mail entra sem ele, com o rastro)."""

    filename: str = Field(default="", max_length=255, pattern=r"^[^\r\n\x00]*$")
    content_type: str = Field(default="", max_length=127, pattern=r"^[^\r\n\x00]*$")
    size: int = Field(default=0, ge=0)
    reason: MOTIVOS_ANEXO


class MessageInV2(MessageIn):
    """O e-mail do v1 MAIS o que só o v2 carrega (tudo vai cifrado, como no v1)."""

    tuta: TutaIn
    delivered_to: list[EmailStr] = Field(default_factory=list, max_length=10)
    text_from_html: bool = False
    omitted_attachments: list[OmittedAttachment] = Field(default_factory=list, max_length=100)
    # Só os cabeçalhos de autenticação (Authentication-Results, DKIM-Signature,
    # Received-SPF…), para o aviso de golpe.
    raw_headers: str | None = Field(default=None, max_length=256 * 1024)

    @model_validator(mode="after")
    def mesmo_id(self) -> "MessageInV2":
        # Um id só para o e-mail: o source_id É o do Tuta (dedupe da Central).
        if self.source_id != f"tuta:{self.tuta.mail_id}":
            raise ValueError("source_id_must_be_tuta_mail_id")
        return self


class IngestV2(StrictModel):
    """Até 20 e-mails; cada um é conferido SOZINHO (um ruim não derruba os outros)."""

    messages: list[dict[str, Any]] = Field(max_length=20)


class CountIn(StrictModel):
    """Os e-mails de uma pasta no Tuta numa janela (só ids)."""

    folder_key: TutaId
    ids: list[SourceId] = Field(default_factory=list, max_length=MAX_IDS_CONTAGEM)
    # `ids` é a janela INTEIRA [since, until) da pasta (senão é só o topo).
    complete: bool = False
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    # O dia fechado (Brasília): grava a linha da conciliação.
    day: date | None = None
    total: int | None = Field(default=None, ge=0, le=10_000_000)


class ChangeIn(StrictModel):
    source_id: SourceId
    folder_key: TutaId | None = None
    deleted: bool = False


class ChangesIn(StrictModel):
    """O que o conector viu mudar no Tuta num e-mail já entregue."""

    changes: list[ChangeIn] = Field(max_length=MAX_MUDANCAS)
