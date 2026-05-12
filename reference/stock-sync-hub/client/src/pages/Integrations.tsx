import DashboardLayout from "@/components/DashboardLayout";
import { PlatformBadge, StatusBadge } from "@/components/PlatformBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { trpc } from "@/lib/trpc";
import { CheckCircle, ExternalLink, Loader2, Pencil, Plus, RefreshCw, ShieldCheck, Trash2, Wifi } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

type Platform = "bling" | "shopee" | "amazon" | "mercadolivre" | "tiktok" | "temu";

const credentialFields: Record<Platform, { key: string; label: string; placeholder: string; type?: string; required?: boolean }[]> = {
  bling: [
    { key: "apiKey", label: "API Key (Bearer Token)", placeholder: "Seu token de acesso do Bling v3", type: "password", required: true },
  ],
  shopee: [
    { key: "partnerId", label: "Partner ID", placeholder: "Ex: 2012455", required: true },
    { key: "partnerKey", label: "Partner Key", placeholder: "Sua chave de parceiro Shopee", type: "password", required: true },
    { key: "shopId", label: "Shop ID", placeholder: "ID da sua loja Shopee", required: true },
  ],
  amazon: [
    { key: "sellerId", label: "Seller ID", placeholder: "Seu ID de vendedor Amazon", required: true },
    { key: "marketplaceId", label: "Marketplace ID", placeholder: "Ex: A2Q3Y263D00KWC (Brasil)", required: true },
    { key: "lwaClientId", label: "LWA Client ID", placeholder: "Client ID do app LWA", required: true },
    { key: "lwaClientSecret", label: "LWA Client Secret", placeholder: "Client Secret do app LWA", type: "password", required: true },
    { key: "refreshToken", label: "Refresh Token", placeholder: "Token de atualização OAuth", type: "password", required: true },
    { key: "region", label: "Região", placeholder: "Ex: us-east-1" },
  ],
  mercadolivre: [
    { key: "clientId", label: "Client ID (App ID)", placeholder: "ID do seu aplicativo ML", required: true },
    { key: "clientSecret", label: "Client Secret", placeholder: "Chave secreta do aplicativo", type: "password", required: true },
    { key: "refreshToken", label: "Refresh Token", placeholder: "Token de atualização OAuth", type: "password" },
    { key: "userId", label: "User ID (opcional)", placeholder: "Seu ID de usuário ML" },
  ],
  tiktok: [
    { key: "serviceId", label: "Service ID", placeholder: "Service ID do app no TikTok Partner Center (ex: 7636288...)", required: true },
    { key: "appKey", label: "App Key", placeholder: "App Key do TikTok (ex: 6juhu0s94v6su)", required: true },
    { key: "appSecret", label: "App Secret", placeholder: "App Secret do TikTok", type: "password", required: true },
  ],
  temu: [
    { key: "appKey", label: "App Key", placeholder: "App Key da Temu Open Platform", required: true },
    { key: "appSecret", label: "App Secret", placeholder: "App Secret da Temu", type: "password", required: true },
    { key: "accessToken", label: "Access Token", placeholder: "Token de acesso da API", type: "password", required: true },
    { key: "region", label: "Região", placeholder: "global, us ou eu (padrão: global)" },
  ],
};

export default function Integrations() {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [selectedPlatform, setSelectedPlatform] = useState<Platform>("bling");
  const [integrationName, setIntegrationName] = useState("");
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [authorizingId, setAuthorizingId] = useState<number | null>(null);

  // Edit state
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingIntegration, setEditingIntegration] = useState<any>(null);
  const [editName, setEditName] = useState("");
  const [editCredentials, setEditCredentials] = useState<Record<string, string>>({});

  const { data: integrations = [], refetch } = trpc.integrations.list.useQuery();
  const utils = trpc.useUtils();

  // Verifica se voltou do OAuth com sucesso
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const oauthSuccess = params.get("oauth_success");
    const oauthError = params.get("oauth_error");

    if (oauthSuccess) {
      toast.success(`Autorização ${oauthSuccess} concluída com sucesso! Clique em "Testar Conexão" para verificar.`);
      window.history.replaceState({}, "", "/integrations");
      utils.integrations.list.invalidate();
    }
    if (oauthError) {
      toast.error(`Erro na autorização: ${decodeURIComponent(oauthError)}`);
      window.history.replaceState({}, "", "/integrations");
    }
  }, []);

  const createMutation = trpc.integrations.create.useMutation({
    onSuccess: () => {
      toast.success("Integração criada com sucesso!");
      setIsDialogOpen(false);
      setCredentials({});
      setIntegrationName("");
      utils.integrations.list.invalidate();
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const updateMutation = trpc.integrations.update.useMutation({
    onSuccess: () => {
      toast.success("Integração atualizada com sucesso!");
      setEditDialogOpen(false);
      setEditingIntegration(null);
      utils.integrations.list.invalidate();
    },
    onError: (err) => toast.error(`Erro ao atualizar: ${err.message}`),
  });

  const deleteMutation = trpc.integrations.delete.useMutation({
    onSuccess: () => { toast.success("Integração removida."); utils.integrations.list.invalidate(); },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const testMutation = trpc.integrations.testConnection.useMutation({
    onSuccess: (result) => {
      setTestingId(null);
      if (result.success) toast.success(result.message);
      else toast.error(result.message);
      utils.integrations.list.invalidate();
    },
    onError: (err) => { setTestingId(null); toast.error(`Erro: ${err.message}`); },
  });

  const handleCreate = () => {
    if (!integrationName.trim()) { toast.error("Informe um nome para a integração"); return; }
    createMutation.mutate({ platform: selectedPlatform, name: integrationName, credentials });
  };

  const handleTest = (id: number) => {
    setTestingId(id);
    testMutation.mutate({ id });
  };

  const handleEdit = (integration: any) => {
    setEditingIntegration(integration);
    setEditName(integration.name);
    // Parse existing credentials if available
    try {
      const creds = typeof integration.credentials === "string"
        ? JSON.parse(integration.credentials)
        : integration.credentials || {};
      setEditCredentials(creds);
    } catch {
      setEditCredentials({});
    }
    setEditDialogOpen(true);
  };

  const handleSaveEdit = () => {
    if (!editingIntegration) return;
    if (!editName.trim()) { toast.error("Informe um nome para a integração"); return; }

    // Only send credentials that have been changed (non-empty)
    const changedCredentials: Record<string, string> = {};
    let hasCredentialChanges = false;
    for (const [key, value] of Object.entries(editCredentials)) {
      if (value && value.trim()) {
        changedCredentials[key] = value.trim();
        hasCredentialChanges = true;
      }
    }

    updateMutation.mutate({
      id: editingIntegration.id,
      name: editName.trim(),
      ...(hasCredentialChanges ? { credentials: changedCredentials } : {}),
    });
  };

  const handleAuthorizeShopee = async (integrationId: number) => {
    try {
      setAuthorizingId(integrationId);
      const origin = window.location.origin;
      const response = await fetch(`/api/marketplace-oauth/shopee/start?integrationId=${integrationId}&origin=${encodeURIComponent(origin)}`, {
        credentials: "include",
      });
      const data = await response.json();
      if (data.authUrl) {
        window.location.href = data.authUrl;
      } else {
        toast.error(data.error || "Erro ao gerar URL de autorização");
        setAuthorizingId(null);
      }
    } catch (err: any) {
      toast.error(`Erro: ${err.message}`);
      setAuthorizingId(null);
    }
  };

  const handleAuthorizeMercadoLivre = async (integrationId: number) => {
    try {
      setAuthorizingId(integrationId);
      const origin = window.location.origin;
      const response = await fetch(`/api/marketplace-oauth/mercadolivre/start?integrationId=${integrationId}&origin=${encodeURIComponent(origin)}`, {
        credentials: "include",
      });
      const data = await response.json();
      if (data.authUrl) {
        window.location.href = data.authUrl;
      } else {
        toast.error(data.error || "Erro ao gerar URL de autorização");
        setAuthorizingId(null);
      }
    } catch (err: any) {
      toast.error(`Erro: ${err.message}`);
      setAuthorizingId(null);
    }
  };

  const handleAuthorizeTikTok = async (integrationId: number) => {
    try {
      setAuthorizingId(integrationId);
      const origin = window.location.origin;
      const response = await fetch(`/api/marketplace-oauth/tiktok/start?integrationId=${integrationId}&origin=${encodeURIComponent(origin)}`, {
        credentials: "include",
      });
      const data = await response.json();
      if (data.authUrl) {
        window.location.href = data.authUrl;
      } else {
        toast.error(data.error || "Erro ao gerar URL de autorização do TikTok");
        setAuthorizingId(null);
      }
    } catch (err: any) {
      toast.error(`Erro: ${err.message}`);
      setAuthorizingId(null);
    }
  };

  const handleAuthorizeBling = async (integrationId: number) => {
    try {
      setAuthorizingId(integrationId);
      const origin = window.location.origin;
      const response = await fetch(`/api/marketplace-oauth/bling/start?integrationId=${integrationId}&origin=${encodeURIComponent(origin)}`, {
        credentials: "include",
      });
      const data = await response.json();
      if (data.authUrl) {
        window.location.href = data.authUrl;
      } else {
        toast.error(data.error || "Erro ao gerar URL de autorização do Bling");
        setAuthorizingId(null);
      }
    } catch (err: any) {
      toast.error(`Erro: ${err.message}`);
      setAuthorizingId(null);
    }
  };

  const needsAuthorization = (integration: any) => {
    if (integration.platform === "shopee" && integration.status !== "connected") return true;
    if (integration.platform === "mercadolivre" && integration.status !== "connected") return true;
    if (integration.platform === "tiktok" && integration.status !== "connected") return true;
    if (integration.platform === "bling" && integration.status !== "connected") return true;
    return false;
  };

  const editPlatform = editingIntegration?.platform as Platform | undefined;

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Integrações</h1>
            <p className="text-muted-foreground text-sm mt-1">Gerencie suas conexões com marketplaces e Bling</p>
          </div>
          <Button onClick={() => setIsDialogOpen(true)} className="gap-2">
            <Plus className="h-4 w-4" />
            Nova Integração
          </Button>
        </div>

        {integrations.length === 0 ? (
          <Card className="border-dashed border-border/50">
            <CardContent className="flex flex-col items-center justify-center py-16">
              <Wifi className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">Nenhuma integração configurada</h3>
              <p className="text-muted-foreground text-sm text-center max-w-md mb-6">
                Adicione suas credenciais do Bling, Shopee, Amazon e Mercado Livre para começar a sincronizar seus estoques automaticamente.
              </p>
              <Button onClick={() => setIsDialogOpen(true)} className="gap-2">
                <Plus className="h-4 w-4" />
                Adicionar Primeira Integração
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {integrations.map(integration => (
              <Card key={integration.id} className="border-border/50">
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <PlatformBadge platform={integration.platform as any} />
                      <CardTitle className="text-base">{integration.name}</CardTitle>
                    </div>
                    <StatusBadge status={integration.status as any} />
                  </div>
                  {integration.lastSyncAt && (
                    <CardDescription className="text-xs">
                      Última sincronização: {new Date(integration.lastSyncAt).toLocaleString("pt-BR")}
                    </CardDescription>
                  )}
                  {integration.errorMessage && (
                    <p className="text-xs text-red-600 mt-1">{integration.errorMessage}</p>
                  )}
                </CardHeader>
                <CardContent>
                  <div className="flex gap-2 flex-wrap">
                    {/* Botão de Autorizar para Shopee */}
                    {integration.platform === "shopee" && integration.status !== "connected" && (
                      <Button
                        size="sm"
                        className="gap-2 bg-orange-600 hover:bg-orange-700 text-white flex-1"
                        onClick={() => handleAuthorizeShopee(integration.id)}
                        disabled={authorizingId === integration.id}
                      >
                        {authorizingId === integration.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5" />
                        )}
                        Autorizar Shopee
                      </Button>
                    )}

                    {/* Botão de Autorizar para Mercado Livre */}
                    {integration.platform === "mercadolivre" && integration.status !== "connected" && (
                      <Button
                        size="sm"
                        className="gap-2 bg-yellow-500 hover:bg-yellow-600 text-white flex-1"
                        onClick={() => handleAuthorizeMercadoLivre(integration.id)}
                        disabled={authorizingId === integration.id}
                      >
                        {authorizingId === integration.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5" />
                        )}
                        Autorizar ML
                      </Button>
                    )}

                    {/* Botão de Autorizar para TikTok Shop */}
                    {integration.platform === "tiktok" && integration.status !== "connected" && (
                      <Button
                        size="sm"
                        className="gap-2 bg-black hover:bg-gray-800 text-white flex-1"
                        onClick={() => handleAuthorizeTikTok(integration.id)}
                        disabled={authorizingId === integration.id}
                      >
                        {authorizingId === integration.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5" />
                        )}
                        Autorizar TikTok
                      </Button>
                    )}

                    {/* Botão de Autorizar para Bling */}
                    {integration.platform === "bling" && integration.status !== "connected" && (
                      <Button
                        size="sm"
                        className="gap-2 bg-blue-600 hover:bg-blue-700 text-white flex-1"
                        onClick={() => handleAuthorizeBling(integration.id)}
                        disabled={authorizingId === integration.id}
                      >
                        {authorizingId === integration.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5" />
                        )}
                        Autorizar Bling
                      </Button>
                    )}

                    {/* Botão Editar */}
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-2"
                      onClick={() => handleEdit(integration)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      Editar
                    </Button>

                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-2 flex-1"
                      onClick={() => handleTest(integration.id)}
                      disabled={testingId === integration.id}
                    >
                      {testingId === integration.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : integration.status === "connected" ? (
                        <CheckCircle className="h-3.5 w-3.5 text-emerald-600" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                      Testar Conexão
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="text-red-600 hover:text-red-700 hover:bg-red-50 border-red-200"
                      onClick={() => deleteMutation.mutate({ id: integration.id })}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>

                  {/* Dica para Shopee/ML que precisam de autorização */}
                  {needsAuthorization(integration) && (
                    <p className="text-xs text-amber-600/80 mt-2">
                      Clique em "Autorizar" para conectar via OAuth. Você será redirecionado para a plataforma.
                    </p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* Create Integration Dialog */}
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Nova Integração</DialogTitle>
              <DialogDescription>Configure as credenciais de acesso à API do marketplace.</DialogDescription>
            </DialogHeader>

            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Plataforma</Label>
                <Select value={selectedPlatform} onValueChange={(v) => { setSelectedPlatform(v as Platform); setCredentials({}); }}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="bling">Bling (ERP)</SelectItem>
                    <SelectItem value="shopee">Shopee</SelectItem>
                    <SelectItem value="amazon">Amazon</SelectItem>
                    <SelectItem value="mercadolivre">Mercado Livre</SelectItem>
                    <SelectItem value="tiktok">TikTok Shop</SelectItem>
                    <SelectItem value="temu">Temu</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>Nome da Integração</Label>
                <Input
                  placeholder="Ex: Bling Principal, Shopee Loja 1..."
                  value={integrationName}
                  onChange={(e) => setIntegrationName(e.target.value)}
                />
              </div>

              {/* Dica para Shopee */}
              {selectedPlatform === "shopee" && (
                <div className="rounded-md bg-orange-50 border border-orange-200 p-3">
                  <p className="text-xs text-orange-300">
                    <strong>Passo 1:</strong> Preencha o Partner ID, Partner Key e Shop ID abaixo e salve.
                  </p>
                  <p className="text-xs text-orange-300 mt-1">
                    <strong>Passo 2:</strong> Após salvar, clique em "Autorizar Shopee" no card da integração para obter o Access Token automaticamente via OAuth.
                  </p>
                </div>
              )}

              {/* Dica para Mercado Livre */}
              {selectedPlatform === "mercadolivre" && (
                <div className="rounded-md bg-yellow-50 border border-yellow-200 p-3">
                  <p className="text-xs text-yellow-300">
                    <strong>Passo 1:</strong> Preencha o Client ID e Client Secret abaixo e salve.
                  </p>
                  <p className="text-xs text-yellow-300 mt-1">
                    <strong>Passo 2:</strong> Após salvar, clique em "Autorizar ML" no card da integração para conectar via OAuth.
                  </p>
                </div>
              )}

              {/* Dica para TikTok Shop */}
              {selectedPlatform === "tiktok" && (
                <div className="rounded-md bg-pink-50 border border-pink-200 p-3">
                  <p className="text-xs text-pink-300">
                    <strong>TikTok Shop:</strong> Cole o Service ID do seu app (número grande no topo da página do app no Partner Center).
                  </p>
                  <p className="text-xs text-pink-300 mt-1">
                    <strong>Passo 1:</strong> Salve com o Service ID. <strong>Passo 2:</strong> Clique em "Autorizar TikTok" no card da integração para conectar via OAuth automaticamente.
                  </p>
                </div>
              )}

              {/* Dica para Temu */}
              {selectedPlatform === "temu" && (
                <div className="rounded-md bg-purple-50 border border-purple-200 p-3">
                  <p className="text-xs text-purple-300">
                    <strong>Temu:</strong> Obtenha as credenciais na Temu Open Platform (partner.temu.com).
                  </p>
                  <p className="text-xs text-purple-300 mt-1">
                    Crie um app para obter App Key e App Secret. O Access Token é gerado via autorização. Região padrão: global.
                  </p>
                </div>
              )}

              {credentialFields[selectedPlatform].map(field => (
                <div key={field.key} className="space-y-2">
                  <Label>
                    {field.label}
                    {field.required && <span className="text-red-600 ml-1">*</span>}
                  </Label>
                  <Input
                    type={field.type ?? "text"}
                    placeholder={field.placeholder}
                    value={credentials[field.key] ?? ""}
                    onChange={(e) => setCredentials(prev => ({ ...prev, [field.key]: e.target.value }))}
                  />
                </div>
              ))}
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={() => setIsDialogOpen(false)}>Cancelar</Button>
              <Button onClick={handleCreate} disabled={createMutation.isPending}>
                {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Salvar Integração
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Edit Integration Dialog */}
        <Dialog open={editDialogOpen} onOpenChange={(open) => { setEditDialogOpen(open); if (!open) setEditingIntegration(null); }}>
          <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Pencil className="h-4 w-4" />
                Editar Integração
              </DialogTitle>
              <DialogDescription>
                Altere o nome ou as credenciais da integração. Campos de credencial em branco serão mantidos como estão.
              </DialogDescription>
            </DialogHeader>

            {editingIntegration && (
              <div className="space-y-4">
                {/* Platform (read-only) */}
                <div className="space-y-2">
                  <Label>Plataforma</Label>
                  <div className="flex items-center gap-2 p-2 rounded-md bg-muted/50">
                    <PlatformBadge platform={editingIntegration.platform as any} />
                    <span className="text-sm font-medium capitalize">{editingIntegration.platform}</span>
                  </div>
                </div>

                {/* Name (editable) */}
                <div className="space-y-2">
                  <Label>Nome da Integração</Label>
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="Nome da integração"
                  />
                </div>

                {/* Credentials (editable) */}
                {editPlatform && credentialFields[editPlatform] && (
                  <>
                    <div className="border-t pt-3">
                      <p className="text-xs text-muted-foreground mb-3">
                        Deixe os campos em branco para manter os valores atuais. Preencha apenas o que deseja alterar.
                      </p>
                    </div>
                    {credentialFields[editPlatform].map(field => (
                      <div key={field.key} className="space-y-2">
                        <Label>
                          {field.label}
                          {field.required && <span className="text-red-600 ml-1">*</span>}
                        </Label>
                        <Input
                          type={field.type ?? "text"}
                          placeholder={editCredentials[field.key] ? "••••••• (valor atual salvo)" : field.placeholder}
                          value={editCredentials[field.key] ?? ""}
                          onChange={(e) => setEditCredentials(prev => ({ ...prev, [field.key]: e.target.value }))}
                        />
                      </div>
                    ))}
                  </>
                )}

                {/* Active toggle */}
                <div className="flex items-center gap-3 border-t pt-3">
                  <Label className="text-sm">Status:</Label>
                  <StatusBadge status={editingIntegration.status as any} />
                </div>
              </div>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={() => { setEditDialogOpen(false); setEditingIntegration(null); }}>
                Cancelar
              </Button>
              <Button onClick={handleSaveEdit} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Salvar Alterações
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </DashboardLayout>
  );
}
