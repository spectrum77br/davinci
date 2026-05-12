import DashboardLayout from "@/components/DashboardLayout";
import { PlatformBadge, StatusBadge } from "@/components/PlatformBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { trpc } from "@/lib/trpc";
import { AlertTriangle, CheckCircle, Clock, GitCompareArrows, Package, Plug, RefreshCw, TrendingUp, X, XCircle, Zap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useLocation } from "wouter";

const platformLabels: Record<string, string> = {
  bling: "Bling",
  shopee: "Shopee",
  amazon: "Amazon",
  mercadolivre: "Mercado Livre",
};

export default function Dashboard() {
  const [isSyncing, setIsSyncing] = useState(false);
  const [dismissedSyncBanner, setDismissedSyncBanner] = useState(false);
  const [, setLocation] = useLocation();

  const { data: integrations = [] } = trpc.integrations.list.useQuery(undefined, { staleTime: 30_000 });
  const { data: products = [] } = trpc.products.list.useQuery(undefined, { staleTime: 30_000 });
  const { data: stats } = trpc.sync.getStats.useQuery(undefined, { staleTime: 30_000 });
  const { data: logs = [] } = trpc.sync.getLogs.useQuery({ limit: 5 }, { staleTime: 30_000 });
  const { data: alerts = [] } = trpc.alerts.list.useQuery({ limit: 5 }, { staleTime: 30_000 });
  const { data: unreadCount = 0 } = trpc.alerts.unreadCount.useQuery(undefined, { staleTime: 30_000 });
  const { data: discrepancies = [] } = trpc.discrepancies.list.useQuery(undefined, { staleTime: 60_000 });
  const { data: lastDailySync } = trpc.alerts.lastDailySync.useQuery(undefined, { staleTime: 30_000 });

  const utils = trpc.useUtils();
  const syncAll = trpc.sync.syncAll.useMutation({
    onSuccess: (result) => {
      setIsSyncing(false);
      if (result.errors > 0) {
        toast.error(`Sincronização concluída com ${result.errors} erro(s). ${result.synced} produto(s) sincronizados.`);
      } else {
        toast.success(`Sincronização concluída! ${result.synced} produto(s) sincronizados com sucesso.`);
      }
      utils.products.list.invalidate();
      utils.sync.getLogs.invalidate();
      utils.alerts.list.invalidate();
      utils.alerts.unreadCount.invalidate();
      utils.integrations.list.invalidate();
    },
    onError: (err) => {
      setIsSyncing(false);
      toast.error(`Erro na sincronização: ${err.message}`);
    },
  });

  const handleSyncAll = () => {
    setIsSyncing(true);
    syncAll.mutate();
  };

  const connectedCount = integrations.filter(i => i.status === "connected").length;
  const activeProducts = products.filter(p => p.isActive).length;
  const lowStockProducts = products.filter(p => p.isActive && (p.blingStock ?? 0) <= (p.lowStockThreshold ?? 5)).length;

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
            <p className="text-muted-foreground text-sm mt-1">Visão geral da sincronização de estoques</p>
          </div>
          <Button
            onClick={handleSyncAll}
            disabled={isSyncing || integrations.length === 0}
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
            {isSyncing ? "Sincronizando..." : "Sincronizar Tudo"}
          </Button>
        </div>

        {/* Banner da Última Sync Diária */}
        {lastDailySync && !dismissedSyncBanner && (() => {
          const isSuccess = lastDailySync.type === "sync_success";
          const isRecent = (Date.now() - new Date(lastDailySync.createdAt).getTime()) < 24 * 60 * 60 * 1000;
          if (!isRecent) return null;
          return (
            <Card className={`border-2 ${isSuccess ? 'border-emerald-300 bg-emerald-50/80' : 'border-red-300 bg-red-50/80'} shadow-sm`}>
              <CardContent className="p-4">
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 p-1.5 rounded-full ${isSuccess ? 'bg-emerald-100' : 'bg-red-100'}`}>
                    {isSuccess
                      ? <CheckCircle className="h-5 w-5 text-emerald-600" />
                      : <XCircle className="h-5 w-5 text-red-600" />
                    }
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={`text-sm font-semibold ${isSuccess ? 'text-emerald-800' : 'text-red-800'}`}>
                      {lastDailySync.title}
                    </p>
                    <p className={`text-xs mt-0.5 ${isSuccess ? 'text-emerald-700' : 'text-red-700'}`}>
                      {lastDailySync.message}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1.5">
                      <Clock className="h-3 w-3 text-muted-foreground" />
                      <span className="text-[11px] text-muted-foreground">
                        {new Date(lastDailySync.createdAt).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => setDismissedSyncBanner(true)}
                    className="text-muted-foreground hover:text-foreground p-1 rounded-md hover:bg-black/5 transition-colors"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </CardContent>
            </Card>
          );
        })()}

        {/* Metrics Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="border-border/50">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">Integrações Ativas</p>
                  <p className="text-3xl font-bold mt-1">{connectedCount}<span className="text-muted-foreground text-lg font-normal">/{integrations.length}</span></p>
                </div>
                <div className="h-10 w-10 rounded-xl bg-blue-50 flex items-center justify-center">
                  <Plug className="h-5 w-5 text-blue-600" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/50">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">Produtos Mapeados</p>
                  <p className="text-3xl font-bold mt-1">{activeProducts}</p>
                </div>
                <div className="h-10 w-10 rounded-xl bg-emerald-50 flex items-center justify-center">
                  <Package className="h-5 w-5 text-emerald-600" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/50">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">Sincronizações OK</p>
                  <p className="text-3xl font-bold mt-1 text-emerald-600">{stats?.success ?? 0}</p>
                </div>
                <div className="h-10 w-10 rounded-xl bg-emerald-50 flex items-center justify-center">
                  <CheckCircle className="h-5 w-5 text-emerald-600" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/50">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wide">Erros / Alertas</p>
                  <p className="text-3xl font-bold mt-1 text-red-600">{stats?.error ?? 0}<span className="text-yellow-600 text-lg font-normal ml-1">/{unreadCount}</span></p>
                </div>
                <div className="h-10 w-10 rounded-xl bg-red-50 flex items-center justify-center">
                  <XCircle className="h-5 w-5 text-red-600" />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Integrations Status */}
          <Card className="border-border/50">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-semibold">Status das Integrações</CardTitle>
                <Button variant="ghost" size="sm" onClick={() => setLocation("/integrations")} className="text-xs text-muted-foreground">
                  Ver todas
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {integrations.length === 0 ? (
                <div className="text-center py-8">
                  <Plug className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">Nenhuma integração configurada</p>
                  <Button size="sm" className="mt-3" onClick={() => setLocation("/integrations")}>
                    Adicionar Integração
                  </Button>
                </div>
              ) : (
                integrations.map(integration => (
                  <div key={integration.id} className="flex items-center justify-between p-3 rounded-lg bg-card/50 border border-border/30">
                    <div className="flex items-center gap-3">
                      <PlatformBadge platform={integration.platform as any} />
                      <span className="text-sm font-medium">{integration.name}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      {integration.lastSyncAt && (
                        <span className="text-xs text-muted-foreground">
                          {new Date(integration.lastSyncAt).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      )}
                      <StatusBadge status={integration.status as any} />
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          {/* Recent Alerts */}
          <Card className="border-border/50">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  Alertas Recentes
                  {unreadCount > 0 && (
                    <span className="bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5 min-w-[20px] text-center">{unreadCount}</span>
                  )}
                </CardTitle>
                <Button variant="ghost" size="sm" onClick={() => setLocation("/alerts")} className="text-xs text-muted-foreground">
                  Ver todos
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {alerts.length === 0 ? (
                <div className="text-center py-8">
                  <CheckCircle className="h-8 w-8 text-emerald-600 mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">Nenhum alerta no momento</p>
                </div>
              ) : (
                alerts.slice(0, 5).map(alert => (
                  <div key={alert.id} className={`flex items-start gap-3 p-3 rounded-lg border ${!alert.isRead ? "bg-card/50 border-border/50" : "bg-card/20 border-border/20"}`}>
                    <div className={`mt-0.5 ${alert.severity === "critical" || alert.severity === "error" ? "text-red-600" : alert.severity === "warning" ? "text-yellow-600" : "text-blue-600"}`}>
                      <AlertTriangle className="h-4 w-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{alert.title}</p>
                      <p className="text-xs text-muted-foreground truncate">{alert.message}</p>
                    </div>
                    {!alert.isRead && <div className="w-2 h-2 rounded-full bg-blue-400 mt-1.5 shrink-0" />}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>

        {/* Recent Sync Logs */}
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base font-semibold">Últimas Sincronizações</CardTitle>
              <Button variant="ghost" size="sm" onClick={() => setLocation("/logs")} className="text-xs text-muted-foreground">
                Ver histórico completo
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {logs.length === 0 ? (
              <div className="text-center py-8">
                <Zap className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                <p className="text-sm text-muted-foreground">Nenhuma sincronização realizada ainda</p>
              </div>
            ) : (
              <div className="space-y-2">
                {logs.map(log => (
                  <div key={log.id} className="flex items-center gap-4 p-3 rounded-lg bg-card/30 border border-border/20">
                    <div className={`h-2 w-2 rounded-full shrink-0 ${log.status === "success" ? "bg-emerald-500" : log.status === "error" ? "bg-red-500" : "bg-yellow-500"}`} />
                    <PlatformBadge platform={log.platform as any} className="shrink-0" />
                    <span className="text-sm flex-1 truncate">{log.message}</span>
                    <span className="text-xs text-muted-foreground shrink-0">
                      {new Date(log.createdAt).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Discrepâncias de Estoque */}
        <Card className={`border-border/50 ${discrepancies.length > 0 ? 'border-orange-200' : ''}`}>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <GitCompareArrows className="h-4 w-4" />
                Discrepâncias de Estoque
                {discrepancies.length > 0 && (
                  <span className="bg-orange-500 text-white text-xs rounded-full px-1.5 py-0.5 min-w-[20px] text-center">{discrepancies.length}</span>
                )}
              </CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            {discrepancies.length === 0 ? (
              <div className="text-center py-6">
                <CheckCircle className="h-8 w-8 text-emerald-600 mx-auto mb-2" />
                <p className="text-sm text-muted-foreground">Todos os estoques estão alinhados</p>
                <p className="text-xs text-muted-foreground mt-1">Bling e marketplaces com diferença ≤ 3 unidades</p>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground mb-3">
                  Produtos com diferença &gt; 3 unidades entre Bling e marketplaces
                </p>
                {discrepancies.slice(0, 8).map(d => (
                  <div key={d.productId} className="p-3 rounded-lg bg-card/30 border border-border/20">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium truncate">{d.sku}</span>
                      <span className="text-xs font-mono bg-blue-50 text-blue-600 px-2 py-0.5 rounded">Bling: {d.blingStock}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {d.links.slice(0, 4).map((link, i) => (
                        <span key={i} className={`text-xs px-2 py-0.5 rounded font-mono ${
                          link.difference > 10 ? 'bg-red-50 text-red-600' : 'bg-orange-50 text-orange-600'
                        }`}>
                          {link.integrationName}: {link.linkStock} (dif: {link.difference})
                        </span>
                      ))}
                      {d.links.length > 4 && (
                        <span className="text-xs text-muted-foreground">+{d.links.length - 4} mais</span>
                      )}
                    </div>
                  </div>
                ))}
                {discrepancies.length > 8 && (
                  <p className="text-xs text-muted-foreground text-center pt-2">
                    ... e mais {discrepancies.length - 8} produto(s) com discrepância
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Low Stock Warning */}
        {lowStockProducts > 0 && (
          <Card className="border-yellow-200 bg-yellow-50">
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-yellow-600 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-yellow-600">{lowStockProducts} produto(s) com estoque baixo</p>
                  <p className="text-xs text-muted-foreground">Verifique seus produtos e reponha o estoque no Bling.</p>
                </div>
                <Button size="sm" variant="outline" className="ml-auto border-yellow-200 text-yellow-600 hover:bg-yellow-50" onClick={() => setLocation("/products")}>
                  Ver Produtos
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </DashboardLayout>
  );
}
