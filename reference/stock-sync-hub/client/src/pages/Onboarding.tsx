import DashboardLayout from "@/components/DashboardLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { trpc } from "@/lib/trpc";
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  ExternalLink,
  Package,
  Plug,
  RefreshCw,
  Rocket,
  Settings,
  Zap,
} from "lucide-react";
import { useLocation } from "wouter";

type StepStatus = "completed" | "current" | "pending";

interface Step {
  id: number;
  title: string;
  description: string;
  icon: React.ElementType;
  action: string;
  actionPath: string;
  tip: string;
}

const STEPS: Step[] = [
  {
    id: 1,
    title: "Conectar o Bling",
    description: "Configure sua API Key do Bling para que a plataforma possa ler e atualizar seus estoques.",
    icon: Plug,
    action: "Ir para Integrações",
    actionPath: "/integrations",
    tip: "No Bling, acesse Configurações → API → Gerar Token. Copie o Bearer Token e cole aqui.",
  },
  {
    id: 2,
    title: "Conectar os Marketplaces",
    description: "Autorize o acesso à Shopee, Amazon e Mercado Livre para sincronização bidirecional.",
    icon: Zap,
    action: "Configurar Marketplaces",
    actionPath: "/integrations",
    tip: "Para Shopee e Mercado Livre, use o botão 'Conectar via OAuth' — é mais seguro e não requer copiar tokens.",
  },
  {
    id: 3,
    title: "Mapear seus Produtos",
    description: "Vincule cada produto do Bling com os IDs correspondentes em cada marketplace.",
    icon: Package,
    action: "Mapear Produtos",
    actionPath: "/products",
    tip: "Tem muitos produtos? Use a importação em massa via CSV para mapear todos de uma vez.",
  },
  {
    id: 4,
    title: "Ativar Sincronização",
    description: "Dispare a primeira sincronização manual para validar que tudo está funcionando.",
    icon: RefreshCw,
    action: "Sincronizar Agora",
    actionPath: "/",
    tip: "Após a primeira sincronização bem-sucedida, o sistema passará a sincronizar automaticamente a cada 15 minutos.",
  },
  {
    id: 5,
    title: "Configurar Alertas",
    description: "Defina limites de estoque mínimo e ative notificações para nunca perder uma venda.",
    icon: Settings,
    action: "Configurar Alertas",
    actionPath: "/settings",
    tip: "Recomendamos configurar alertas de estoque baixo para pelo menos 5 unidades por produto.",
  },
];

function getStepStatus(stepId: number, integrationCount: number, productCount: number, syncCount: number): StepStatus {
  if (stepId === 1) return integrationCount > 0 ? "completed" : "current";
  if (stepId === 2) return integrationCount >= 2 ? "completed" : integrationCount > 0 ? "current" : "pending";
  if (stepId === 3) return productCount > 0 ? "completed" : integrationCount >= 2 ? "current" : "pending";
  if (stepId === 4) return syncCount > 0 ? "completed" : productCount > 0 ? "current" : "pending";
  if (stepId === 5) {
    const prev = syncCount > 0 ? "current" : "pending";
    return prev === "current" ? "current" : "pending";
  }
  return "pending";
}

export default function Onboarding() {
  const [, navigate] = useLocation();

  const { data: integrations = [] } = trpc.integrations.list.useQuery();
  const { data: products = [] } = trpc.products.list.useQuery();
  const { data: syncStats } = trpc.sync.getStats.useQuery();

  const integrationCount = integrations.filter(i => i.status === "connected").length;
  const productCount = products.length;
  const syncCount = syncStats?.total ?? 0;

  const steps = STEPS.map(step => ({
    ...step,
    status: getStepStatus(step.id, integrationCount, productCount, syncCount),
  }));

  const completedCount = steps.filter(s => s.status === "completed").length;
  const progressPercent = Math.round((completedCount / STEPS.length) * 100);
  const isComplete = completedCount === STEPS.length;

  return (
    <DashboardLayout>
      <div className="max-w-3xl mx-auto space-y-8">
        {/* Header */}
        <div className="text-center space-y-3">
          <div className="flex justify-center">
            <div className="p-3 rounded-full bg-primary/10 border border-primary/20">
              <Rocket className="h-8 w-8 text-primary" />
            </div>
          </div>
          <h1 className="text-3xl font-bold">Configure sua plataforma</h1>
          <p className="text-muted-foreground max-w-lg mx-auto">
            Siga os passos abaixo para conectar seus marketplaces e ativar a sincronização automática de estoques.
          </p>
        </div>

        {/* Progress */}
        <Card className="border-border/50">
          <CardContent className="pt-6">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium">Progresso da configuração</span>
              <span className="text-sm text-muted-foreground">{completedCount} de {STEPS.length} etapas concluídas</span>
            </div>
            <Progress value={progressPercent} className="h-2" />
            {isComplete && (
              <div className="flex items-center gap-2 mt-3 text-emerald-600 text-sm">
                <CheckCircle2 className="h-4 w-4" />
                <span className="font-medium">Configuração completa! Sua plataforma está totalmente operacional.</span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Steps */}
        <div className="space-y-4">
          {steps.map((step, index) => {
            const Icon = step.icon;
            const isCompleted = step.status === "completed";
            const isCurrent = step.status === "current";
            const isPending = step.status === "pending";

            return (
              <Card
                key={step.id}
                className={`border transition-all duration-200 ${
                  isCompleted
                    ? "border-emerald-200 bg-emerald-50"
                    : isCurrent
                    ? "border-primary/50 bg-primary/5 shadow-sm shadow-primary/10"
                    : "border-border/30 opacity-60"
                }`}
              >
                <CardContent className="pt-5 pb-5">
                  <div className="flex items-start gap-4">
                    {/* Step indicator */}
                    <div className="flex-shrink-0 mt-0.5">
                      {isCompleted ? (
                        <CheckCircle2 className="h-6 w-6 text-emerald-600" />
                      ) : isCurrent ? (
                        <div className="h-6 w-6 rounded-full border-2 border-primary flex items-center justify-center">
                          <span className="text-xs font-bold text-primary">{step.id}</span>
                        </div>
                      ) : (
                        <Circle className="h-6 w-6 text-muted-foreground/40" />
                      )}
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className={`font-semibold text-sm ${isPending ? "text-muted-foreground" : "text-foreground"}`}>
                          {step.title}
                        </h3>
                        {isCompleted && (
                          <Badge variant="outline" className="text-emerald-600 border-emerald-200 bg-emerald-50 text-xs py-0">
                            Concluído
                          </Badge>
                        )}
                        {isCurrent && (
                          <Badge variant="outline" className="text-primary border-primary/30 bg-primary/10 text-xs py-0">
                            Próximo passo
                          </Badge>
                        )}
                      </div>
                      <p className={`text-sm mb-3 ${isPending ? "text-muted-foreground/60" : "text-muted-foreground"}`}>
                        {step.description}
                      </p>

                      {/* Tip */}
                      {(isCurrent || isCompleted) && (
                        <div className="flex items-start gap-2 text-xs text-muted-foreground bg-muted/30 rounded-lg p-3 mb-3">
                          <span className="text-primary font-bold flex-shrink-0">💡</span>
                          <span>{step.tip}</span>
                        </div>
                      )}

                      {/* Action button */}
                      {!isPending && (
                        <Button
                          size="sm"
                          variant={isCurrent ? "default" : "outline"}
                          className="gap-2"
                          onClick={() => navigate(step.actionPath)}
                        >
                          <Icon className="h-3.5 w-3.5" />
                          {step.action}
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Help section */}
        <Card className="border-border/30 bg-muted/10">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Precisa de ajuda?</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 pb-5">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {[
                { label: "Documentação da API Bling", url: "https://developer.bling.com.br/", icon: ExternalLink },
                { label: "API da Shopee (Open Platform)", url: "https://open.shopee.com/", icon: ExternalLink },
                { label: "API do Mercado Livre", url: "https://developers.mercadolivre.com.br/", icon: ExternalLink },
              ].map(link => (
                <a
                  key={link.url}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors p-2 rounded-lg hover:bg-muted/30"
                >
                  <link.icon className="h-3.5 w-3.5 flex-shrink-0" />
                  {link.label}
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
