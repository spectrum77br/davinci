"""Mac worker contract: HTML is inert encrypted content; no account passwords."""

import base64
import binascii
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

Header = Annotated[str, Field(max_length=998, pattern=r"^[^\r\n\x00]*$")]
ErrorCode = Annotated[str, Field(max_length=64, pattern=r"^[a-z0-9_:-]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# A shared Tuta account can carry many aliases (the company one has 54 plus
# the main address); the list is both "who received" and "who may reply".
MAX_ALIASES = 200


class MailboxCreate(StrictModel):
    label: str = Field(min_length=1, max_length=120)
    address: EmailStr
    aliases: list[EmailStr] = Field(default_factory=list, max_length=MAX_ALIASES)
    owner_user_id: UUID | None = None


class MailboxPatch(StrictModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    send_enabled: bool | None = None
    # Replaces the whole list; the main address always stays in it.
    aliases: list[EmailStr] | None = Field(default=None, max_length=MAX_ALIASES)


class AttachmentIn(StrictModel):
    filename: str = Field(min_length=1, max_length=255, pattern=r"^[^\r\n\x00]*$")
    content_type: str = Field(
        default="application/octet-stream",
        max_length=127,
        pattern=r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$",
    )
    data_base64: str = Field(max_length=14 * 1024 * 1024)
    content_id: str | None = Field(default=None, max_length=255)
    disposition: Literal["inline", "attachment"] = "attachment"

    @field_validator("content_id", mode="before")
    @classmethod
    def normalized_content_id(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError("invalid_content_id")
        value = value.strip(" ")
        if value.startswith("<") and value.endswith(">"):
            value = value[1:-1]
        if not value or any(ord(c) < 33 or c in "<>" for c in value):
            raise ValueError("invalid_content_id")
        return value

    @field_validator("data_base64")
    @classmethod
    def valid_data(cls, value: str) -> str:
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid_base64") from error
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("attachment_too_large")
        return value


class MessageIn(StrictModel):
    source_id: str = Field(min_length=1, max_length=191, pattern=r"^[^\x00-\x1f]*$")
    folder: str = Field(min_length=1, max_length=128)
    direction: Literal["inbound", "sent"] = "inbound"
    received_at: AwareDatetime
    subject: Header = ""
    from_address: EmailStr
    from_name: str = Field(default="", max_length=256)
    to: list[EmailStr] = Field(default_factory=list, max_length=100)
    cc: list[EmailStr] = Field(default_factory=list, max_length=100)
    reply_to: EmailStr | None = None
    message_id: Header = ""
    in_reply_to: Header | None = None
    references: list[Header] = Field(default_factory=list, max_length=50)
    text: str = Field(default="", max_length=2 * 1024 * 1024)
    html: str | None = Field(default=None, max_length=2 * 1024 * 1024)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)

    @field_validator("html")
    @classmethod
    def limited_html(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("html_too_large")
        return value

    @model_validator(mode="after")
    def unique_inline_ids(self):
        ids = [a.content_id for a in self.attachments if a.content_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_content_id")
        return self


class Ingest(StrictModel):
    messages: list[MessageIn] = Field(max_length=20)
    enrich_existing_only: bool = Field(default=False, strict=True)


class Heartbeat(StrictModel):
    state: Literal["online", "login_required", "error"]
    can_send: bool = False
    error_code: ErrorCode | None = None


class ReplyIn(StrictModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=100_000)
    from_address: EmailStr | None = None

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("empty_or_invalid_text")
        return value


class OutboxResolve(StrictModel):
    """A person checked Tuta: did the ambiguous reply go out (`saiu`)?"""

    saiu: bool


class Receipt(StrictModel):
    lease_token: str = Field(min_length=20, max_length=128)
    status: Literal["sent", "failed", "uncertain"]
    message_id: Header | None = None
    error_code: ErrorCode | None = None
