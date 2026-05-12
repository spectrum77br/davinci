import DashboardLayout from "@/components/DashboardLayout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { trpc } from "@/lib/trpc";
import { Loader2, Save, Settings as SettingsIcon, Zap, Copy, CheckCircle2, ExternalLink, Clock } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

function WebhookCard() {
  const { data: webhookInfo, isLoading } = trpc.settings.webhookUrl.useQuery();
  const [copied, setCopied] = useState(false);

  const copyUrl = () => {
    if (webhookInfo?.url) {
      navigator.clipboard.writeText(webhookInfo.url);
      setCopied(true);
      toast.success("URL copiada!");
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <Card className="border-amber-200 bg-amber-50">
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Zap className="h-4 w-4 text-amber-500" />
          Sync Instantâneo (Anti-Overselling)
        </CardTitle>
        <CardDescription>
          Configure o webhook do Bling para receber notificações de estoque em tempo real.
          Quando um produto com estoque baixo vender, o sistema atualiza todas as contas instantaneamente.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label className="text-sm font-medium">URL do Webhook</Label>
          <div className="flex gap-2">
            <Input
              readOnly
              value={isLoading ? "Carregando..." : (webhookInfo?.url ?? "")}
              className="font-mono text-xs bg-background"
            />
            <Button variant="outline" size="icon" onClick={copyUrl} className="shrink-0">
              {copied ? <CheckCircle2 className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        <div className="rounded-lg bg-background/50 border border-border/30 p-3 space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Como configurar no Bling:</p>
          <ol className="text-xs text-muted-foreground space-y-1 list-decimal list-inside">
            <li>Acesse <a href="https://developer.bling.com.br" target="_blank" rel="noopener" className="text-amber-500 hover:underline inline-flex items-center gap-0.5">developer.bling.com.br <ExternalLink className="h-3 w-3" /></a> e entre no seu aplicativo</li>
            <li>Vá na aba <strong>"Webhooks"</strong></li>
            <li>Adicione um servidor com a URL acima</li>
            <li>Ative o recurso <strong>"Estoque"</strong> com as ações <strong>"created"</strong> e <strong>"updated"</strong></li>
            <li>Salve as configurações</li>
          </ol>
        </div>

        <div className="flex items-start gap-2 text-xs text-muted-foreground bg-background/50 border border-border/30 rounded-lg p-3">
          <Zap className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
          <div>
            <strong>Como funciona:</strong> Quando uma venda acontece, o Bling notifica o Stock Sync Hub em segundos.
            Se o produto tiver estoque ≤ limite configurado, o sistema atualiza automaticamente em todas as contas
            (ML, Shopee, Amazon, etc.) para evitar overselling.
            <br />
            <strong>Fallback:</strong> Mesmo sem webhook, o sistema verifica produtos com estoque baixo a cada 5 minutos.
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Settings() {
  const { data: settings, isLoading } = trpc.settings.get.useQuery();
  const utils = trpc.useUtils();

  const [form, setForm] = useState({
    syncIntervalMinutes: 15,
    lowStockThreshold: 5,
    emailNotifications: true,
    inAppNotifications: true,
    notifyOnSyncError: true,
    notifyOnLowStock: true,
    notifyOnDiscrepancy: true,
    autoSync: true,
    dailySyncTime: "00:00",
  });

  useEffect(() => {
    if (settings) {
      setForm({
        syncIntervalMinutes: settings.syncIntervalMinutes ?? 15,
        lowStockThreshold: settings.lowStockThreshold ?? 5,
        emailNotifications: settings.emailNotifications ?? true,
        inAppNotifications: settings.inAppNotifications ?? true,
        notifyOnSyncError: settings.notifyOnSyncError ?? true,
        notifyOnLowStock: settings.notifyOnLowStock ?? true,
        notifyOnDiscrepancy: settings.notifyOnDiscrepancy ?? true,
        autoSync: settings.autoSync ?? true,
        dailySyncTime: settings.dailySyncTime ?? "00:00",
      });
    }
  }, [settings]);

  const updateMutation = trpc.settings.update.useMutation({
    onSuccess: () => {
      toast.success("Configurações salvas com sucesso!");
      utils.settings.get.invalidate();
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const handleSave = () => {
    updateMutation.mutate(form);
  };

  if (isLoading) {
    return (
      <DashboardLayout>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="space-y-6 max-w-2xl">
        <div>
          <h1 className="text-2xl font-bold">Configurações</h1>
          <p className="text-muted-foreground text-sm mt-1">Gerencie as preferências de sincronização e notificações</p>
        </div>

        {/* Sync Settings */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <SettingsIcon className="h-4 w-4" />
              Sincronização Automática
            </CardTitle>
            <CardDescription>Configure como e quando os estoques são sincronizados</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex items-center justify-between">
              <div>
                <Label className="text-sm font-medium">Sincronização Automática</Label>
                <p className="text-xs text-muted-foreground mt-0.5">Sincroniza automaticamente todos os estoques uma vez por dia</p>
              </div>
              <Switch
                checked={form.autoSync}
                onCheckedChange={(v) => setForm(p => ({ ...p, autoSync: v }))}
              />
            </div>

            <div className="space-y-2">
              <Label className="flex items-center gap-2">
                <Clock className="h-4 w-4 text-muted-foreground" />
                Horário da Sync Diária (Brasília)
              </Label>
              <Input
                type="time"
                value={form.dailySyncTime}
                onChange={e => setForm(p => ({ ...p, dailySyncTime: e.target.value }))}
                disabled={!form.autoSync}
                className="max-w-xs"
              />
              <p className="text-xs text-muted-foreground">
                A sincronização completa será executada todos os dias neste horário (fuso horário de Brasília).
                Todos os produtos ativos serão sincronizados em todas as contas.
              </p>
            </div>

            <div className="space-y-2">
              <Label>Limite de Estoque Baixo (unidades)</Label>
              <Input
                type="number"
                min={0}
                value={form.lowStockThreshold}
                onChange={e => setForm(p => ({ ...p, lowStockThreshold: parseInt(e.target.value) || 0 }))}
                className="max-w-xs"
              />
              <p className="text-xs text-muted-foreground">Produtos com estoque igual ou abaixo deste valor terão <strong>sync instantâneo</strong> via webhook do Bling (anti-overselling).</p>
            </div>
          </CardContent>
        </Card>

        {/* Webhook / Instant Sync */}
        <WebhookCard />

        {/* Notification Settings */}
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-base">Notificações</CardTitle>
            <CardDescription>Configure quais eventos geram notificações</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex items-center justify-between">
              <div>
                <Label className="text-sm font-medium">Notificações no App</Label>
                <p className="text-xs text-muted-foreground mt-0.5">Alertas visíveis dentro da plataforma</p>
              </div>
              <Switch
                checked={form.inAppNotifications}
                onCheckedChange={(v) => setForm(p => ({ ...p, inAppNotifications: v }))}
              />
            </div>

            <div className="flex items-center justify-between">
              <div>
                <Label className="text-sm font-medium">Notificações por Email</Label>
                <p className="text-xs text-muted-foreground mt-0.5">Receba alertas importantes por email</p>
              </div>
              <Switch
                checked={form.emailNotifications}
                onCheckedChange={(v) => setForm(p => ({ ...p, emailNotifications: v }))}
              />
            </div>

            <div className="border-t border-border/30 pt-4 space-y-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Tipos de Alerta</p>

              <div className="flex items-center justify-between">
                <div>
                  <Label className="text-sm font-medium">Erros de Sincronização</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">Notificar quando uma sincronização falhar</p>
                </div>
                <Switch
                  checked={form.notifyOnSyncError}
                  onCheckedChange={(v) => setForm(p => ({ ...p, notifyOnSyncError: v }))}
                />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label className="text-sm font-medium">Estoque Baixo</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">Notificar quando um produto atingir o limite mínimo</p>
                </div>
                <Switch
                  checked={form.notifyOnLowStock}
                  onCheckedChange={(v) => setForm(p => ({ ...p, notifyOnLowStock: v }))}
                />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label className="text-sm font-medium">Discrepâncias de Estoque</Label>
                  <p className="text-xs text-muted-foreground mt-0.5">Notificar quando o estoque divergir entre plataformas</p>
                </div>
                <Switch
                  checked={form.notifyOnDiscrepancy}
                  onCheckedChange={(v) => setForm(p => ({ ...p, notifyOnDiscrepancy: v }))}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Button onClick={handleSave} disabled={updateMutation.isPending} className="gap-2">
          {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Salvar Configurações
        </Button>
      </div>
    </DashboardLayout>
  );
}
