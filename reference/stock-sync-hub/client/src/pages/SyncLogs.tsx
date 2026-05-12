import DashboardLayout from "@/components/DashboardLayout";
import { PlatformBadge } from "@/components/PlatformBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { trpc } from "@/lib/trpc";
import { CheckCircle, ChevronDown, ChevronRight, RefreshCw, Search, XCircle, AlertTriangle, Zap } from "lucide-react";
import { Fragment } from "react";
import { useState } from "react";
import { toast } from "sonner";

export default function SyncLogs() {
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [isSyncing, setIsSyncing] = useState(false);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  const { data: logs = [], refetch } = trpc.sync.getLogs.useQuery({ limit: 200 }, { staleTime: 15_000 });
  const { data: stats } = trpc.sync.getStats.useQuery(undefined, { staleTime: 15_000 });
  const utils = trpc.useUtils();

  const syncAll = trpc.sync.syncAll.useMutation({
    onSuccess: (result) => {
      setIsSyncing(false);
      if (result.errors > 0) toast.error(`Concluído com ${result.errors} erro(s). ${result.synced} sincronizados.`);
      else toast.success(`${result.synced} produto(s) sincronizados com sucesso!`);
      utils.sync.getLogs.invalidate();
      utils.sync.getStats.invalidate();
    },
    onError: (err) => { setIsSyncing(false); toast.error(err.message); },
  });

  const filtered = logs.filter(log => {
    const matchSearch = log.message.toLowerCase().includes(searchQuery.toLowerCase()) || log.action.toLowerCase().includes(searchQuery.toLowerCase());
    const matchStatus = statusFilter === "all" || log.status === statusFilter;
    const matchPlatform = platformFilter === "all" || log.platform === platformFilter;
    return matchSearch && matchStatus && matchPlatform;
  });

  const toggleRow = (id: number) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const parseDetails = (details: string | null | undefined): string[] => {
    if (!details || details === "NULL") return [];
    try {
      const parsed = JSON.parse(details);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  };

  const statusIcon = (status: string) => {
    if (status === "success") return <CheckCircle className="h-4 w-4 text-emerald-600" />;
    if (status === "error") return <XCircle className="h-4 w-4 text-red-600" />;
    return <AlertTriangle className="h-4 w-4 text-yellow-600" />;
  };

  const statusBadge = (status: string) => {
    if (status === "success") return <Badge variant="outline" className="text-emerald-600 border-emerald-200 bg-emerald-50">Sucesso</Badge>;
    if (status === "error") return <Badge variant="outline" className="text-red-600 border-red-200 bg-red-50">Erro</Badge>;
    return <Badge variant="outline" className="text-yellow-600 border-yellow-200 bg-yellow-50">Aviso</Badge>;
  };

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Sincronizações</h1>
            <p className="text-muted-foreground text-sm mt-1">Histórico completo de sincronizações e logs de erros</p>
          </div>
          <Button onClick={() => { setIsSyncing(true); syncAll.mutate(); }} disabled={isSyncing} className="gap-2">
            <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
            {isSyncing ? "Sincronizando..." : "Sincronizar Agora"}
          </Button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4">
          <Card className="border-border/50">
            <CardContent className="p-4 flex items-center gap-3">
              <CheckCircle className="h-8 w-8 text-emerald-600 shrink-0" />
              <div>
                <p className="text-2xl font-bold text-emerald-600">{stats?.success ?? 0}</p>
                <p className="text-xs text-muted-foreground">Sucesso</p>
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4 flex items-center gap-3">
              <XCircle className="h-8 w-8 text-red-600 shrink-0" />
              <div>
                <p className="text-2xl font-bold text-red-600">{stats?.error ?? 0}</p>
                <p className="text-xs text-muted-foreground">Erros</p>
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4 flex items-center gap-3">
              <AlertTriangle className="h-8 w-8 text-yellow-600 shrink-0" />
              <div>
                <p className="text-2xl font-bold text-yellow-600">{stats?.warning ?? 0}</p>
                <p className="text-xs text-muted-foreground">Avisos</p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Filters */}
        <div className="flex gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input placeholder="Buscar por SKU, conta ou mensagem..." className="pl-9" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
          </div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-36">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todos</SelectItem>
              <SelectItem value="success">Sucesso</SelectItem>
              <SelectItem value="error">Erro</SelectItem>
              <SelectItem value="warning">Aviso</SelectItem>
            </SelectContent>
          </Select>
          <Select value={platformFilter} onValueChange={setPlatformFilter}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="Plataforma" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas</SelectItem>
              <SelectItem value="bling">Bling</SelectItem>
              <SelectItem value="shopee">Shopee</SelectItem>
              <SelectItem value="amazon">Amazon</SelectItem>
              <SelectItem value="mercadolivre">Mercado Livre</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Logs Table */}
        {filtered.length === 0 ? (
          <Card className="border-dashed border-border/50">
            <CardContent className="flex flex-col items-center justify-center py-16">
              <Zap className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">Nenhum log encontrado</h3>
              <p className="text-muted-foreground text-sm">Execute uma sincronização para ver os logs aqui.</p>
            </CardContent>
          </Card>
        ) : (
          <Card className="border-border/50">
            <Table>
              <TableHeader>
                <TableRow className="border-border/50 hover:bg-transparent">
                  <TableHead className="w-8"></TableHead>
                  <TableHead>Plataforma</TableHead>
                  <TableHead>Ação</TableHead>
                  <TableHead>Mensagem</TableHead>
                  <TableHead className="text-center">Estoque</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Data/Hora</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map(log => {
                  const details = parseDetails(log.details);
                  const hasDetails = details.length > 0;
                  const isExpanded = expandedRows.has(log.id);
                  
                  return (
                    <Fragment key={log.id}>
                      <TableRow
                        className={`border-border/30 ${hasDetails ? 'cursor-pointer hover:bg-muted/50' : ''}`}
                        onClick={() => hasDetails && toggleRow(log.id)}
                      >
                        <TableCell>
                          <div className="flex items-center gap-1">
                            {hasDetails && (
                              isExpanded
                                ? <ChevronDown className="h-3 w-3 text-muted-foreground" />
                                : <ChevronRight className="h-3 w-3 text-muted-foreground" />
                            )}
                            {statusIcon(log.status)}
                          </div>
                        </TableCell>
                        <TableCell><PlatformBadge platform={log.platform as any} /></TableCell>
                        <TableCell className="text-xs font-mono text-muted-foreground">{log.action}</TableCell>
                        <TableCell className="text-sm max-w-md">
                          <span className="block truncate" title={log.message}>{log.message}</span>
                        </TableCell>
                        <TableCell className="text-center text-sm">
                          {log.stockBefore !== null && log.stockBefore !== undefined && log.stockAfter !== null && log.stockAfter !== undefined ? (
                            <span className="font-mono text-xs">
                              <span className="text-muted-foreground">{log.stockBefore}</span>
                              <span className="text-muted-foreground mx-0.5">→</span>
                              <span className={log.stockAfter > log.stockBefore ? 'text-emerald-600 font-semibold' : log.stockAfter < log.stockBefore ? 'text-red-600 font-semibold' : ''}>{log.stockAfter}</span>
                            </span>
                          ) : log.stockAfter !== null && log.stockAfter !== undefined ? (
                            <span className="font-mono">{log.stockAfter}</span>
                          ) : "—"}
                        </TableCell>
                        <TableCell>{statusBadge(log.status)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                          {new Date(log.createdAt).toLocaleString("pt-BR")}
                        </TableCell>
                      </TableRow>
                      {isExpanded && hasDetails && (
                        <TableRow key={`${log.id}-details`} className="border-border/20 bg-muted/30">
                          <TableCell colSpan={7} className="py-2 px-6">
                            <div className="text-xs space-y-1 font-mono">
                              <p className="text-muted-foreground font-sans text-[11px] mb-1.5 font-medium">Detalhes por conta:</p>
                              {details.map((detail, i) => {
                                const isSuccess = detail.startsWith('✓');
                                const isError = detail.startsWith('✗');
                                const isSkipped = detail.startsWith('⊘');
                                const isFix = detail.startsWith('🔧');
                                return (
                                  <div
                                    key={i}
                                    className={`py-0.5 px-2 rounded ${
                                      isSuccess ? 'text-emerald-700 bg-emerald-50' :
                                      isError ? 'text-red-700 bg-red-50' :
                                      isSkipped ? 'text-gray-600 bg-gray-50' :
                                      isFix ? 'text-amber-700 bg-amber-50' :
                                      'text-muted-foreground'
                                    }`}
                                  >
                                    {detail}
                                  </div>
                                );
                              })}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>
    </DashboardLayout>
  );
}
