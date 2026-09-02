from typing import Literal, get_args

from pydantic import BaseModel, RootModel, model_validator

Resource = Literal[
    "produtos",
    "anuncios",
    # Marketing — dashboards de Ads (ML/Shopee) e aba Criativos (briefing
    # + arquivos + aprovação → MEGA). Mesmos nomes usados no useCan e nos
    # require_permission dos routers marketing.py / marketing_creatives.py.
    "marketing",
    "marketing_criativos",
    "tabela_precos",
    "tabela_precos_contas",
    "tabela_precos_produtos",
    "tabela_precos_concorrencia",
    "margem",
    "faturamento",
    "controle_estoque",
    "devolucoes",
    "reembolso",
    # Logística — casos de pós-venda a acompanhar + aba Status. Sugestão de
    # status Bling a partir da assinatura de status do Meli.
    "logistica",
    # Notas Fiscais — consulta/export de NF-e das contas bling_notas.
    "notas_fiscais",
    # Chamados — aba de Pós-venda que centraliza os chamados abertos nas
    # plataformas (origem Margem/Logística/Devolução), com histórico,
    # réplica manual/automática e alterar status Bling.
    "chamados",
    # Legacy single-bucket — no longer used by any route after the
    # financeiro_* split below, but kept in the literal so stored
    # permissions JSON containing the old key still validates.
    "financeiro",
    # Per-planilha financeiro grants. Each backs a sidebar entry and
    # a path-prefix in apps/api/app/routers/financeiro.py.
    "financeiro_consorcio",
    "financeiro_suprimentos",
    "financeiro_simulacao",
    "financeiro_dnp",
    # Legacy — Valuation virou admin-only (require_admin no router), o
    # resource não é mais usado em runtime. Mantido aqui pra JSONB salvo
    # antes da mudança não falhar na validação (mesmo precedente do
    # "financeiro" legacy acima).
    "financeiro_valuation",
    # Importação module — controle de pedidos de importação (malas/China)
    "importacao",
    "sincronizacoes",
    "sync_logs",
    "integracoes",
    "alertas",
    "empresa",
    "cadastro",
    "lojas_info",
    # NF automáticas — cadastros (Faturador/Etiqueta/Impressão) e painel de
    # faturamento por etapa. Antes admin-only; viraram recursos concedíveis.
    "nf_faturador",
    "nf_faturamento",
    "segmentos",
    "usuarios",
    "permissoes",
    "configuracoes",
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
