import DashboardLayout from "@/components/DashboardLayout";
import { PlatformBadge } from "@/components/PlatformBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { trpc } from "@/lib/trpc";
import { AlertTriangle, Bell, BellOff, CheckCheck, Info, XCircle } from "lucide-react";
import { toast } from "sonner";

const severityConfig = {
  info: { icon: Info, color: "text-blue-600", bg: "bg-blue-50 border-blue-200", label: "Info" },
  warning: { icon: AlertTriangle, color: "text-yellow-600", bg: "bg-yellow-50 border-yellow-200", label: "Aviso" },
  error: { icon: XCircle, color: "text-red-600", bg: "bg-red-50 border-red-200", label: "Erro" },
  critical: { icon: XCircle, color: "text-red-500", bg: "bg-red-50 border-red-200", label: "Crítico" },
};

export default function Alerts() {
  const { data: alerts = [], refetch } = trpc.alerts.list.useQuery({ limit: 100 }, { staleTime: 15_000 });
  const { data: unreadCount = 0 } = trpc.alerts.unreadCount.useQuery(undefined, { staleTime: 15_000 });
  const utils = trpc.useUtils();

  const markReadMutation = trpc.alerts.markRead.useMutation({
    onSuccess: () => {
      utils.alerts.list.invalidate();
      utils.alerts.unreadCount.invalidate();
    },
  });

  const markAllReadMutation = trpc.alerts.markAllRead.useMutation({
    onSuccess: () => {
      toast.success("Todos os alertas marcados como lidos.");
      utils.alerts.list.invalidate();
      utils.alerts.unreadCount.invalidate();
    },
  });

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-3">
              Alertas
              {unreadCount > 0 && (
                <span className="bg-red-500 text-white text-sm rounded-full px-2 py-0.5 min-w-[24px] text-center">{unreadCount}</span>
              )}
            </h1>
            <p className="text-muted-foreground text-sm mt-1">Notificações de erros, estoque baixo e discrepâncias</p>
          </div>
          {unreadCount > 0 && (
            <Button variant="outline" className="gap-2" onClick={() => markAllReadMutation.mutate()} disabled={markAllReadMutation.isPending}>
              <CheckCheck className="h-4 w-4" />
              Marcar Todos como Lidos
            </Button>
          )}
        </div>

        {alerts.length === 0 ? (
          <Card className="border-dashed border-border/50">
            <CardContent className="flex flex-col items-center justify-center py-16">
              <Bell className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">Nenhum alerta</h3>
              <p className="text-muted-foreground text-sm">Tudo funcionando perfeitamente! Alertas aparecerão aqui quando houver problemas.</p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {alerts.map(alert => {
              const config = severityConfig[alert.severity as keyof typeof severityConfig] ?? severityConfig.info;
              const Icon = config.icon;
              return (
                <div
                  key={alert.id}
                  className={`flex items-start gap-4 p-4 rounded-lg border transition-all ${!alert.isRead ? `${config.bg}` : "bg-card/20 border-border/20 opacity-60"}`}
                  onClick={() => !alert.isRead && markReadMutation.mutate({ id: alert.id })}
                  style={{ cursor: !alert.isRead ? "pointer" : "default" }}
                >
                  <Icon className={`h-5 w-5 mt-0.5 shrink-0 ${config.color}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-sm font-semibold">{alert.title}</p>
                      <Badge variant="outline" className={`text-xs ${config.color} border-current/30`}>{config.label}</Badge>
                      {alert.platform && <PlatformBadge platform={alert.platform as any} />}
                      {!alert.isRead && <div className="w-2 h-2 rounded-full bg-blue-400 ml-auto shrink-0" />}
                    </div>
                    <p className="text-sm text-muted-foreground mt-1">{alert.message}</p>
                    <p className="text-xs text-muted-foreground mt-2">
                      {new Date(alert.createdAt).toLocaleString("pt-BR")}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
