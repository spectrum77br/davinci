"""Complete, read-only Tuta bridge inventory. No email body or credentials."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.mail import StrictModel

MESSAGE_ID_PATTERN = r"^<[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+@tutabridge\.local>$"


class BridgeFolder(StrictModel):
    id: str = Field(min_length=1, max_length=191, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    kind: Literal["inbox", "sent", "drafts", "trash", "spam", "archive", "custom"]


class BridgePlacement(StrictModel):
    message_id: str = Field(max_length=512, pattern=MESSAGE_ID_PATTERN)
    folder_id: str = Field(min_length=1, max_length=191)


class BridgeSnapshot(StrictModel):
    version: Literal[1]
    complete: Literal[True]
    observed_at: AwareDatetime
    folders: list[BridgeFolder] = Field(min_length=3, max_length=1000)
    messages: list[BridgePlacement] = Field(max_length=50000)

    @model_validator(mode="after")
    def coherent_inventory(self):
        ids = {f.id for f in self.folders}
        names = {f.name for f in self.folders}
        if len(ids) != len(self.folders) or len(names) != len(self.folders):
            raise ValueError("duplicate_folder")
        kinds = [f.kind for f in self.folders if f.kind != "custom"]
        if len(kinds) != len(set(kinds)) or not {"inbox", "trash", "archive"} <= set(kinds):
            raise ValueError("incomplete_system_folders")
        message_ids = {m.message_id for m in self.messages}
        if len(message_ids) != len(self.messages):
            raise ValueError("duplicate_message")
        if any(m.folder_id not in ids for m in self.messages):
            raise ValueError("unknown_folder")
        return self
