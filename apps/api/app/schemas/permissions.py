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
    # Solta a cerca de EQUIPE só dentro da Tabela de Preços (Eduardo, 10/09:
    # o israel é da equipe 2 e precisa ver todas as contas ali, e só ali).
    # Lida em routers/pricing.py:_escopo_precos; as outras abas seguem
    # cercadas por sales_teams.
    "tabela_precos_todas_contas",
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
    # Atendimento (25/09/2026) — caixa única das conversas de Shopee, ML,
    # TikTok e Amazon, com rascunho da IA. view lê a fila; edit responde e
    # mexe no manual/respostas prontas; delete apaga regra/modelo. Mesmo
    # nome no useCan.ts. SÓ ADMIN por enquanto (30/09/2026): o router exige
    # admin (SO_ADMIN em routers/atendimento.py) e o recurso saiu da tela de
    # Permissões; fica aqui para o JSON salvo validar e para quando abrir.
    "atendimento",
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
    # NF Faturador › Emissão de Serviço (28/09/2026): NFS-e Nacional de
    # intermediação pelo Emissor Nacional. Emitir/conferir = edit, cancelar =
    # delete. Mesmo nome no useCan.ts.
    "emissao_servico",
    "segmentos",
    # Cadastros › Marcas e Redes Sociais (15/09/2026): marcas da operação
    # (INPI, domínio, login) e as contas por plataforma — que mais tarde
    # recebem a auto-postagem de vídeos. Mesmos nomes no useCan.ts.
    "marcas",
    "redes_sociais",
    # Cadastros › E-mails: padrões de e-mail por marca e canal (logo,
    # assinatura, prévia e envio de teste).
    "email_padroes",
    # Ouvidoria › Robôs (21/09/2026): catálogo dos robôs (modo, última
    # rodada, quem avisa) + ocorrências que eles abriram. Grupo próprio no
    # menu, entre Cadastros e Admin — mesmo nome no useCan.ts.
    "ouvidoria",
    # Denúncia (30/09/2026): Anúncios, Denúncias e Casos da fiscalização da
    # marca — cópia que o Mac mini da Makisa manda. Só leitura (view); grupo
    # próprio no menu, logo abaixo de Ouvidoria. Mesmo nome no useCan.ts.
    "denuncia",
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
