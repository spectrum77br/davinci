from typing import Literal, get_args

from pydantic import BaseModel, RootModel, model_validator

Resource = Literal[
    "produtos",
    "anuncios",
    "tabela_precos",
    "tabela_precos_contas",
    "tabela_precos_produtos",
    "conciliacao_frete",
    "sincronizacoes",
    "devolucoes",
    "reembolso",
    "tarefas",
    "margem",
    "empresa",
    "cadastro",
    "permissoes",
]
RESOURCES: tuple[str, ...] = get_args(Resource)

Action = Literal["view", "edit", "delete"]


class ResourcePerm(BaseModel):
    view: bool = False
    edit: bool = False
    delete: bool = False

    @model_validator(mode="after")
    def cascade(self) -> "ResourcePerm":
        if self.delete:
            self.edit = True
            self.view = True
        elif self.edit:
            self.view = True
        return self


class Permissions(RootModel[dict[Resource, ResourcePerm]]):
    @model_validator(mode="after")
    def fill_defaults(self) -> "Permissions":
        current = self.root or {}
        self.root = {r: current.get(r, ResourcePerm()) for r in RESOURCES}
        return self

    def to_jsonb(self) -> dict:
        return {r: p.model_dump() for r, p in self.root.items()}

    @classmethod
    def from_jsonb(cls, data: dict | None) -> "Permissions":
        return cls.model_validate(data or {})
