import DashboardLayout from "@/components/DashboardLayout";
import { PlatformBadge } from "@/components/PlatformBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { trpc } from "@/lib/trpc";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AlertTriangle, CheckSquare, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Download, FileUp, Filter, Loader2, Package, Play, Plus, RefreshCw, Search, Square, Trash2, Upload, X, Zap } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Checkbox } from "@/components/ui/checkbox";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";

// ── CSV Template ────────────────────────────────────────────────────────────
const CSV_TEMPLATE_HEADER = "sku,nome,id_bling,id_shopee,id_amazon,id_mercadolivre,estoque_minimo";
const CSV_TEMPLATE_EXAMPLE = [
  "CAM-AZL-M,Camiseta Azul M,12345,67890,B08XYZ123,MLB987654321,5",
  "CAM-VML-G,Camiseta Vermelha G,12346,67891,B08XYZ124,MLB987654322,3",
].join("\n");

function downloadCsvTemplate() {
  const content = `${CSV_TEMPLATE_HEADER}\n${CSV_TEMPLATE_EXAMPLE}`;
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "template_produtos_stock_sync.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function parseCsvRows(text: string) {
  const lines = text.trim().split("\n").map(l => l.trim()).filter(Boolean);
  if (lines.length < 2) throw new Error("CSV deve ter pelo menos uma linha de dados além do cabeçalho");

  const header = lines[0].toLowerCase().split(",").map(h => h.trim());
  const rows = [];

  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(",").map(v => v.trim());
    const row: Record<string, string> = {};
    header.forEach((h, idx) => { row[h] = values[idx] || ""; });

    const sku = row["sku"] || row["codigo"] || row["code"];
    const name = row["nome"] || row["name"] || row["produto"] || row["product"];

    if (!sku || !name) continue;

    rows.push({
      sku,
      name,
      blingId: row["id_bling"] || row["bling_id"] || undefined,
      shopeeId: row["id_shopee"] || row["shopee_id"] || undefined,
      amazonId: row["id_amazon"] || row["amazon_id"] || undefined,
      mercadolivreId: row["id_mercadolivre"] || row["ml_id"] || row["mercadolivre_id"] || undefined,
      lowStockThreshold: parseInt(row["estoque_minimo"] || row["min_stock"] || "5") || 5,
    });
  }

  return rows;
}

export default function Products() {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isCsvDialogOpen, setIsCsvDialogOpen] = useState(false);
  const [isBlingDialogOpen, setIsBlingDialogOpen] = useState(false);
  const [isAutoLinkDialogOpen, setIsAutoLinkDialogOpen] = useState(false);
  const [autoLinkResult, setAutoLinkResult] = useState<null | {
    details: string[];
    synced: number;
    errors: number;
  }>(null);
  const [autoLinkJobId, setAutoLinkJobId] = useState<string | null>(null);
  const [isAutoLinking, setIsAutoLinking] = useState(false);
  const [selectedBlingIds, setSelectedBlingIds] = useState<Set<string>>(new Set());
  const [blingSearchQuery, setBlingSearchQuery] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterIntegrationId, setFilterIntegrationId] = useState<string>("all");
  const [filterStockLevel, setFilterStockLevel] = useState<string>("all");
  const [selectedAutoLinkIds, setSelectedAutoLinkIds] = useState<Set<number>>(new Set());
  const [isSyncAllDialogOpen, setIsSyncAllDialogOpen] = useState(false);
  const [selectedSyncAllIds, setSelectedSyncAllIds] = useState<Set<number>>(new Set());
  const [syncProductId, setSyncProductId] = useState<number | null>(null);
  const [syncSelectedIds, setSyncSelectedIds] = useState<Set<number>>(new Set());
  // Multi-select for batch sync
  const [selectedProductIds, setSelectedProductIds] = useState<Set<number>>(new Set());
  const [isBulkDeleteDialogOpen, setIsBulkDeleteDialogOpen] = useState(false);
  const [isSyncSelectedDialogOpen, setIsSyncSelectedDialogOpen] = useState(false);
  const [selectedSyncSelectedIntegIds, setSelectedSyncSelectedIntegIds] = useState<Set<number>>(new Set());
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvPreview, setCsvPreview] = useState<ReturnType<typeof parseCsvRows>>([]);
  const [csvError, setCsvError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Manual Amazon link
  const [manualLinkDialogOpen, setManualLinkDialogOpen] = useState(false);
  const [manualLinkProduct, setManualLinkProduct] = useState<any>(null);

  // Delete individual link
  const [deleteLinkTarget, setDeleteLinkTarget] = useState<{id: number; externalId: string; integrationName?: string | null; platform?: string | null} | null>(null);
  const [deleteLinkDialogOpen, setDeleteLinkDialogOpen] = useState(false);

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const ITEMS_PER_PAGE = 50;

  const [form, setForm] = useState({
    sku: "", name: "", blingId: "", shopeeId: "", amazonId: "", mercadolivreId: "", lowStockThreshold: "5",
  });

  const { data: products = [] } = trpc.products.list.useQuery(undefined, { staleTime: 30_000 });
  const { data: productLinks = [] } = trpc.products.getProductLinks.useQuery(undefined, { staleTime: 30_000 });
  const { data: integrations = [] } = trpc.integrations.list.useQuery(undefined, { staleTime: 30_000 });
  const utils = trpc.useUtils();

  // Marketplace integrations (exclude bling)
  const marketplaceIntegrations = useMemo(() => integrations.filter(i => i.platform !== "bling"), [integrations]);
  const connectedMarketplace = useMemo(() => marketplaceIntegrations.filter(i => i.status === "connected"), [marketplaceIntegrations]);

  const createMutation = trpc.products.create.useMutation({
    onSuccess: () => {
      toast.success("Produto criado com sucesso!");
      setIsDialogOpen(false);
      setForm({ sku: "", name: "", blingId: "", shopeeId: "", amazonId: "", mercadolivreId: "", lowStockThreshold: "5" });
      utils.products.list.invalidate();
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const deleteMutation = trpc.products.delete.useMutation({
    onSuccess: () => { toast.success("Produto removido."); utils.products.list.invalidate(); },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const bulkDeleteMutation = trpc.products.bulkDelete.useMutation({
    onSuccess: (result) => {
      toast.success(`${result.deleted} produto(s) excluído(s) com sucesso.`);
      setSelectedProductIds(new Set());
      utils.products.list.invalidate();
    },
    onError: (err) => toast.error(`Erro ao excluir: ${err.message}`),
  });

  const manualLinkAmazonMutation = trpc.products.manualLinkAmazon.useMutation({
    onSuccess: (result) => {
      if (result.alreadyLinked) {
        toast.info(result.message);
      } else {
        toast.success(result.message);
      }
      setManualLinkDialogOpen(false);
      setManualLinkProduct(null);
      utils.products.getProductLinks.invalidate();
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const deleteLinkMutation = trpc.products.deleteLink.useMutation({
    onSuccess: () => {
      toast.success("Link removido com sucesso!");
      setDeleteLinkDialogOpen(false);
      setDeleteLinkTarget(null);
      utils.products.getProductLinks.invalidate();
      utils.products.list.invalidate();
    },
    onError: (err) => toast.error(`Erro ao remover link: ${err.message}`),
  });

  // ── Async Sync All ──────────────────────────────────────────────────────
  const [syncJobId, setSyncJobId] = useState<string | null>(null);
  const [syncJobDone, setSyncJobDone] = useState(false);

  const startSyncAllMutation = trpc.sync.startSyncAll.useMutation({
    onSuccess: (result) => {
      setSyncJobId(result.jobId);
      setSyncJobDone(false);
      if (result.alreadyRunning) {
        toast.info("Uma sincronização já está em andamento.");
      } else {
        toast.info("Sincronização iniciada em segundo plano...");
      }
    },
    onError: (err) => toast.error(`Erro ao iniciar sincronização: ${err.message}`),
  });

  // Poll progress every 2 seconds while job is running
  const { data: syncProgress } = trpc.sync.getSyncProgress.useQuery(
    { jobId: syncJobId! },
    {
      enabled: !!syncJobId && !syncJobDone,
      refetchInterval: 2000,
    }
  );

  // Detect when job completes
  useEffect(() => {
    if (!syncProgress) return;
    if (syncProgress.status === "completed" || syncProgress.status === "error") {
      setSyncJobDone(true);
      utils.products.list.invalidate();
      utils.products.getProductLinks.invalidate();
      if (syncProgress.status === "completed") {
        const skippedVerified = (syncProgress as any).skippedVerified ?? 0;
        const skippedClosed = (syncProgress as any).skippedClosed ?? 0;
        const parts: string[] = [`${syncProgress.synced} sincronizado(s)`];
        if (skippedVerified > 0) parts.push(`${skippedVerified} estoque igual (verificado)`);
        if (skippedClosed > 0) parts.push(`${skippedClosed} fechado/revis\u00e3o`);
        if (syncProgress.errors > 0) parts.push(`${syncProgress.errors} erro(s)`);
        const summary = parts.join(", ");
        if (syncProgress.errors > 0) {
          toast.error(`Sincronização concluída: ${summary}`);
        } else {
          toast.success(`Sincronização concluída! ${summary}`);
        }
      } else {
        toast.error("Erro fatal durante a sincronização.");
      }
    }
  }, [syncProgress?.status]);

  const isSyncing = !!syncJobId && !syncJobDone;
  const syncProgressPercent = syncProgress && syncProgress.total > 0
    ? Math.round((syncProgress.processed / syncProgress.total) * 100)
    : 0;

  const syncProductSelectedMutation = trpc.sync.syncProductSelected.useMutation({
    onSuccess: (result) => {
      const skippedVerified = (result as any).skippedVerified ?? 0;
      const skippedClosed = (result as any).skippedClosed ?? 0;
      const parts: string[] = [];
      if (result.synced > 0) parts.push(`${result.synced} sincronizado(s)`);
      if (skippedVerified > 0) parts.push(`${skippedVerified} estoque igual`);
      if (skippedClosed > 0) parts.push(`${skippedClosed} fechado/revis\u00e3o`);
      if (result.errors > 0) parts.push(`${result.errors} erro(s)`);
      const summary = parts.join(', ') || 'Nenhuma plataforma sincronizada';
      if (result.errors > 0) {
        toast.error(summary, { description: result.details.join('\n') });
      } else if (result.synced > 0) {
        toast.success(summary, { description: result.details.join('\n') });
      } else {
        toast.info(summary, { description: result.details.join('\n') });
      }
      setSyncProductId(null);
      setSyncSelectedIds(new Set());
      utils.products.list.invalidate();
      utils.products.getProductLinks.invalidate();
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  // Busca produtos do Bling (lazy - só executa quando o dialog abre)
  const blingProductsQuery = trpc.products.fetchFromBling.useQuery(undefined, {
    enabled: isBlingDialogOpen,
    staleTime: 30_000, // 30s cache
  });

  const importFromBlingMutation = trpc.products.importFromBling.useMutation({
    onSuccess: (result) => {
      toast.success(`Importação do Bling concluída: ${result.imported} novos, ${result.updated} atualizados${result.errors > 0 ? `, ${result.errors} erros` : ""}.`);
      setIsBlingDialogOpen(false);
      setSelectedBlingIds(new Set());
      utils.products.list.invalidate();
    },
    onError: (err) => toast.error(`Erro ao importar do Bling: ${err.message}`),
  });

  const startAutoLinkMutation = trpc.products.startAutoLink.useMutation({
    onSuccess: (result) => {
      setAutoLinkJobId(result.jobId);
      setIsAutoLinking(true);
      if (result.alreadyRunning) {
        toast.info("Vinculação já em andamento...");
      }
    },
    onError: (err: any) => toast.error(`Erro na vinculação: ${err.message}`),
  });

  // Poll autoLink progress
  const autoLinkProgress = trpc.products.getAutoLinkProgress.useQuery(
    { jobId: autoLinkJobId! },
    {
      enabled: !!autoLinkJobId && isAutoLinking,
      refetchInterval: 2000,
    }
  );

  // Handle autoLink completion
  useEffect(() => {
    if (!autoLinkProgress.data) return;
    const p = autoLinkProgress.data;
    if (p.status === "completed" || p.status === "error") {
      setIsAutoLinking(false);
      setAutoLinkResult({
        details: p.details ?? [],
        synced: p.synced ?? 0,
        errors: p.errors ?? 0,
      });
      utils.products.list.invalidate();
      utils.products.getProductLinks.invalidate();
      if (p.status === "completed" && (p.synced ?? 0) > 0) {
        toast.success(`${p.synced} produto(s) vinculados automaticamente!`);
      } else if (p.status === "completed") {
        toast.info("Nenhum produto novo vinculado. Verifique se os SKUs coincidem.");
      } else {
        toast.error("Erro durante a vinculação automática.");
      }
    }
  }, [autoLinkProgress.data]);

  const importCsvMutation = trpc.products.importCsv.useMutation({
    onSuccess: (result) => {
      toast.success(`Importação concluída: ${result.imported} novos, ${result.updated} atualizados${result.errors > 0 ? `, ${result.errors} erros` : ""}.`);
      setIsCsvDialogOpen(false);
      setCsvFile(null);
      setCsvPreview([]);
      utils.products.list.invalidate();
    },
    onError: (err) => toast.error(`Erro na importação: ${err.message}`),
  });

  const handleCreate = () => {
    if (!form.sku.trim() || !form.name.trim()) { toast.error("SKU e nome são obrigatórios"); return; }
    createMutation.mutate({
      sku: form.sku,
      name: form.name,
      blingId: form.blingId || undefined,
      shopeeId: form.shopeeId || undefined,
      amazonId: form.amazonId || undefined,
      mercadolivreId: form.mercadolivreId || undefined,
      lowStockThreshold: parseInt(form.lowStockThreshold) || 5,
    });
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setCsvFile(file);
    setCsvError(null);

    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const text = ev.target?.result as string;
        const rows = parseCsvRows(text);
        if (rows.length === 0) throw new Error("Nenhuma linha válida encontrada no CSV");
        setCsvPreview(rows);
      } catch (err: any) {
        setCsvError(err.message);
        setCsvPreview([]);
      }
    };
    reader.readAsText(file, "UTF-8");
  };

  const handleImportCsv = () => {
    if (csvPreview.length === 0) return;
    importCsvMutation.mutate({ rows: csvPreview });
  };

  const filtered = products.filter(p => {
    // Text search filter (searches name, SKU, and linked listing title)
    const q = searchQuery.toLowerCase();
    const matchesSearch = !searchQuery ||
      p.name.toLowerCase().includes(q) ||
      p.sku.toLowerCase().includes(q) ||
      (p.listingTitle && p.listingTitle.toLowerCase().includes(q)) ||
      (p.allListingTitles && p.allListingTitles.some((t: string) => t.toLowerCase().includes(q)));
    if (!matchesSearch) return false;

    // Integration filter
    if (filterIntegrationId !== "all") {
      const integId = Number(filterIntegrationId);
      const hasDirectLink =
        p.shopeeIntegrationId === integId ||
        p.amazonIntegrationId === integId ||
        p.mercadolivreIntegrationId === integId;
      const hasProductLink = productLinks.some(l => l.productId === p.id && l.integrationId === integId);
      if (!hasDirectLink && !hasProductLink) return false;
    }

    // Stock level filter
    if (filterStockLevel !== "all") {
      const stock = p.blingStock ?? 0;
      const threshold = p.lowStockThreshold ?? 5;
      if (filterStockLevel === "low" && stock > threshold) return false;
      if (filterStockLevel === "ok" && stock <= threshold) return false;
    }

    return true;
  });

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, filterIntegrationId, filterStockLevel]);

  // Pagination logic
  const totalPages = Math.max(1, Math.ceil(filtered.length / ITEMS_PER_PAGE));
  const paginatedProducts = filtered.slice(
    (currentPage - 1) * ITEMS_PER_PAGE,
    currentPage * ITEMS_PER_PAGE
  );

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Produtos</h1>
            <p className="text-muted-foreground text-sm mt-1">Mapeamento de produtos entre plataformas · <span className="text-foreground font-medium">{filtered.length}</span> produto(s)</p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              className="gap-2 border-emerald-300 text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700"
              onClick={() => { setAutoLinkResult(null); setIsAutoLinkDialogOpen(true); }}
              disabled={startAutoLinkMutation.isPending || isAutoLinking}
            >
              {(startAutoLinkMutation.isPending || isAutoLinking) ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
              Vincular Automático
            </Button>
            <Button variant="outline" className="gap-2" onClick={() => {
              if (isSyncing) {
                // Re-open progress dialog
                setIsSyncAllDialogOpen(true);
              } else {
                setSyncJobDone(false);
                setSyncJobId(null);
                setIsSyncAllDialogOpen(true);
                setSelectedSyncAllIds(new Set(connectedMarketplace.map(i => i.id)));
              }
            }} disabled={startSyncAllMutation.isPending}>
              <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
              {isSyncing ? `Sincronizando... ${syncProgressPercent}%` : "Sincronizar Todos"}
            </Button>
            <Button variant="outline" className="gap-2 border-orange-300 text-orange-600 hover:bg-orange-50 hover:text-orange-700" onClick={() => setIsBlingDialogOpen(true)}>
              <Zap className="h-4 w-4" />
              Importar do Bling
            </Button>
            <Button variant="outline" className="gap-2" onClick={() => setIsCsvDialogOpen(true)}>
              <Upload className="h-4 w-4" />
              Importar CSV
            </Button>
            <Button onClick={() => setIsDialogOpen(true)} className="gap-2">
              <Plus className="h-4 w-4" />
              Novo Produto
            </Button>
          </div>
        </div>

        <div className="flex gap-3 items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Buscar por nome, SKU ou título do anúncio..."
              className="pl-9"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <Select value={filterStockLevel} onValueChange={setFilterStockLevel}>
            <SelectTrigger className="w-[180px] gap-2">
              <SelectValue placeholder="Todo estoque" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todo estoque</SelectItem>
              <SelectItem value="low">
                <span className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-yellow-500"></span>
                  Estoque Baixo
                </span>
              </SelectItem>
              <SelectItem value="ok">
                <span className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-emerald-500"></span>
                  Estoque OK
                </span>
              </SelectItem>
            </SelectContent>
          </Select>
          <Select value={filterIntegrationId} onValueChange={setFilterIntegrationId}>
            <SelectTrigger className="w-[220px] gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <SelectValue placeholder="Todas as contas" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas as contas</SelectItem>
              {marketplaceIntegrations.map(integ => (
                <SelectItem key={integ.id} value={String(integ.id)}>
                  <span className="flex items-center gap-2">
                    <PlatformBadge platform={integ.platform as any} className="text-[10px] px-1 py-0" />
                    {integ.name}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {filtered.length === 0 ? (
          <Card className="border-dashed border-border/50">
            <CardContent className="flex flex-col items-center justify-center py-16">
              <Package className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">{searchQuery ? "Nenhum produto encontrado" : "Nenhum produto mapeado"}</h3>
              <p className="text-muted-foreground text-sm text-center max-w-md mb-6">
                {searchQuery
                  ? "Tente uma busca diferente."
                  : "Adicione produtos individualmente ou importe em massa via CSV para sincronizar os estoques automaticamente."}
              </p>
              {!searchQuery && (
                <div className="flex gap-3">
                  <Button variant="outline" onClick={() => setIsCsvDialogOpen(true)} className="gap-2">
                    <Upload className="h-4 w-4" />
                    Importar CSV
                  </Button>
                  <Button onClick={() => setIsDialogOpen(true)} className="gap-2">
                    <Plus className="h-4 w-4" />
                    Adicionar Produto
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        ) : (
          <Card className="border-border/50">
            <Table>
              <TableHeader>
                <TableRow className="border-border/50 hover:bg-transparent">
                  <TableHead className="w-10">
                    <Checkbox
                      checked={paginatedProducts.length > 0 && paginatedProducts.every(p => selectedProductIds.has(p.id))}
                      onCheckedChange={(checked) => {
                        if (checked) {
                          setSelectedProductIds(prev => {
                            const next = new Set(prev);
                            paginatedProducts.forEach(p => next.add(p.id));
                            return next;
                          });
                        } else {
                          setSelectedProductIds(prev => {
                            const next = new Set(prev);
                            paginatedProducts.forEach(p => next.delete(p.id));
                            return next;
                          });
                        }
                      }}
                    />
                  </TableHead>
                  <TableHead>SKU</TableHead>
                  <TableHead>Produto</TableHead>
                  <TableHead className="text-center">Bling</TableHead>
                  <TableHead className="text-center">Shopee</TableHead>
                  <TableHead className="text-center">Amazon</TableHead>
                  <TableHead className="text-center">ML Clássico</TableHead>
                  <TableHead className="text-center">ML Premium</TableHead>
                  <TableHead className="text-center">TikTok</TableHead>
                  <TableHead className="text-center">Status</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedProducts.map(product => {
                  const isLowStock = (product.blingStock ?? 0) <= (product.lowStockThreshold ?? 5);
                  return (
                    <TableRow key={product.id} className={`border-border/30 ${selectedProductIds.has(product.id) ? 'bg-cyan-50' : ''}`} style={{lineHeight: '1.2'}}>
                      <TableCell>
                        <Checkbox
                          checked={selectedProductIds.has(product.id)}
                          onCheckedChange={(checked) => {
                            setSelectedProductIds(prev => {
                              const next = new Set(prev);
                              if (checked) next.add(product.id); else next.delete(product.id);
                              return next;
                            });
                          }}
                        />
                      </TableCell>
                      <TableCell className="py-1.5">
                        <p className="text-xs font-mono">{product.sku}</p>
                      </TableCell>
                      <TableCell className="py-1.5">
                        <p className="font-medium text-xs leading-tight truncate max-w-[180px]" title={product.name || ""}>
                          {product.name || ""}
                        </p>
                      </TableCell>
                      <TableCell className="text-center py-1.5">
                        <span className={`text-xs font-bold ${isLowStock ? "text-yellow-600" : "text-foreground"}`}>
                          {product.blingStock ?? 0}
                        </span>
                      </TableCell>
                      <TableCell className="text-center py-1.5">
                        {(() => {
                            const allSpLinks = productLinks.filter(l => l.productId === product.id && l.platform === "shopee");
                          const spLinks = filterIntegrationId !== "all" ? allSpLinks.filter(l => l.integrationId === Number(filterIntegrationId)) : allSpLinks;
                          if (spLinks.length > 0) {
                            return (
                              <div className="flex flex-col items-center gap-0.5">
                                {spLinks.map((link, idx) => (
                                  <div key={link.id} className={`group/link relative flex flex-col items-center leading-tight ${idx > 0 ? 'border-t border-border/20 pt-0.5' : ''}`}>
                                    <button
                                      className="absolute -right-3 -top-0.5 hidden group-hover/link:flex items-center justify-center h-3.5 w-3.5 rounded-full bg-red-500 hover:bg-red-600 text-white cursor-pointer"
                                      title="Remover este link"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setDeleteLinkTarget(link);
                                        setDeleteLinkDialogOpen(true);
                                      }}
                                    >
                                      <X className="h-2 w-2" />
                                    </button>
                                    <span className="text-xs font-bold">{link.stock ?? 0}</span>
                                    <span className="text-[9px] text-muted-foreground leading-none">{link.integrationName || `#${link.externalId}`}</span>
                                  </div>
                                ))}
                              </div>
                            );
                          }
                          if (product.shopeeId) {
                            return (
                              <div className="flex flex-col items-center gap-0">
                                <span className="text-xs font-bold">{product.shopeeStock ?? 0}</span>
                                <span className="text-[9px] text-muted-foreground leading-none">{product.shopeeIntegrationName || `#${product.shopeeId}`}</span>
                              </div>
                            );
                          }
                          return <span className="text-[10px] text-muted-foreground">—</span>;
                        })()}
                      </TableCell>
                      <TableCell className="text-center py-1.5">
                        {(() => {
                          const amazonLinks = productLinks.filter(l => l.productId === product.id && l.platform === "amazon");
                          const filteredAmazonLinks = filterIntegrationId !== "all" ? amazonLinks.filter(l => l.integrationId === Number(filterIntegrationId)) : amazonLinks;
                          if (filteredAmazonLinks.length > 0) {
                            return (
                              <div className="flex flex-col items-center gap-0.5">
                                {filteredAmazonLinks.map((link, idx) => (
                                  <div key={link.id} className={`group/link relative flex flex-col items-center leading-tight ${idx > 0 ? 'border-t border-border/20 pt-0.5' : ''}`}>
                                    <button
                                      className="absolute -right-3 -top-0.5 hidden group-hover/link:flex items-center justify-center h-3.5 w-3.5 rounded-full bg-red-500 hover:bg-red-600 text-white cursor-pointer"
                                      title="Remover este link"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setDeleteLinkTarget(link);
                                        setDeleteLinkDialogOpen(true);
                                      }}
                                    >
                                      <X className="h-2 w-2" />
                                    </button>
                                    <span className="text-xs font-bold">{link.stock ?? 0}</span>
                                    <span className="text-[9px] text-muted-foreground leading-none">{link.integrationName || link.externalId}</span>
                                  </div>
                                ))}
                              </div>
                            );
                          }

                          // Show link button if there are Amazon integrations
                          const amazonIntegs = connectedMarketplace.filter(i => i.platform === "amazon");
                          if (amazonIntegs.length > 0) {
                            return (
                              <button
                                className="text-[10px] text-blue-500 hover:text-blue-700 hover:underline cursor-pointer"
                                title="Vincular manualmente à Amazon"
                                onClick={() => {
                                  setManualLinkProduct(product);
                                  setManualLinkDialogOpen(true);
                                }}
                              >
                                + vincular
                              </button>
                            );
                          }
                          return <span className="text-[10px] text-muted-foreground">—</span>;
                        })()}
                      </TableCell>
                      {/* ML Clássico */}
                      <TableCell className="text-center py-1.5">
                        {(() => {
                          const allMlLinks = productLinks.filter(l => l.productId === product.id && l.platform === "mercadolivre");
                          const mlLinks = filterIntegrationId !== "all" ? allMlLinks.filter(l => l.integrationId === Number(filterIntegrationId)) : allMlLinks;
                          const classicoLinks = mlLinks.filter(l => l.listingType === 'gold_special' || l.listingType === 'free' || (!l.listingType && l.listingType !== 'gold_pro'));
                          if (classicoLinks.length > 0) {
                            return (
                              <div className="flex flex-col items-center gap-0.5">
                                {classicoLinks.map((link, idx) => (
                                  <div key={link.id} className={`group/link relative flex flex-col items-center leading-tight ${idx > 0 ? 'border-t border-border/20 pt-0.5' : ''}`}>
                                    <button
                                      className="absolute -right-3 -top-0.5 hidden group-hover/link:flex items-center justify-center h-3.5 w-3.5 rounded-full bg-red-500 hover:bg-red-600 text-white cursor-pointer"
                                      title="Remover este link"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setDeleteLinkTarget(link);
                                        setDeleteLinkDialogOpen(true);
                                      }}
                                    >
                                      <X className="h-2 w-2" />
                                    </button>
                                    <span className="text-xs font-bold">{link.stock ?? 0}</span>
                                    <span className="text-[9px] text-muted-foreground leading-none">{link.integrationName || link.externalId}</span>
                                  </div>
                                ))}
                              </div>
                            );
                          }
                          return <span className="text-[10px] text-muted-foreground">—</span>;
                        })()}
                      </TableCell>
                      {/* ML Premium */}
                      <TableCell className="text-center py-1.5">
                        {(() => {
                          const allMlLinks = productLinks.filter(l => l.productId === product.id && l.platform === "mercadolivre");
                          const mlLinks = filterIntegrationId !== "all" ? allMlLinks.filter(l => l.integrationId === Number(filterIntegrationId)) : allMlLinks;
                          const premiumLinks = mlLinks.filter(l => l.listingType === 'gold_pro');
                          if (premiumLinks.length > 0) {
                            return (
                              <div className="flex flex-col items-center gap-0.5">
                                {premiumLinks.map((link, idx) => (
                                  <div key={link.id} className={`group/link relative flex flex-col items-center leading-tight ${idx > 0 ? 'border-t border-border/20 pt-0.5' : ''}`}>
                                    <button
                                      className="absolute -right-3 -top-0.5 hidden group-hover/link:flex items-center justify-center h-3.5 w-3.5 rounded-full bg-red-500 hover:bg-red-600 text-white cursor-pointer"
                                      title="Remover este link"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setDeleteLinkTarget(link);
                                        setDeleteLinkDialogOpen(true);
                                      }}
                                    >
                                      <X className="h-2 w-2" />
                                    </button>
                                    <span className="text-xs font-bold">{link.stock ?? 0}</span>
                                    <span className="text-[9px] text-muted-foreground leading-none">{link.integrationName || link.externalId}</span>
                                  </div>
                                ))}
                              </div>
                            );
                          }
                          return <span className="text-[10px] text-muted-foreground">—</span>;
                        })()}
                      </TableCell>
                      {/* TikTok */}
                      <TableCell className="text-center py-1.5">
                        {(() => {
                          const allTikTokLinks = productLinks.filter(l => l.productId === product.id && l.platform === "tiktok");
                          const tikTokLinks = filterIntegrationId !== "all" ? allTikTokLinks.filter(l => l.integrationId === Number(filterIntegrationId)) : allTikTokLinks;
                          if (tikTokLinks.length > 0) {
                            return (
                              <div className="flex flex-col items-center gap-0.5">
                                {tikTokLinks.map((link, idx) => (
                                  <div key={link.id} className={`group/link relative flex flex-col items-center leading-tight ${idx > 0 ? 'border-t border-border/20 pt-0.5' : ''}`}>
                                    <button
                                      className="absolute -right-3 -top-0.5 hidden group-hover/link:flex items-center justify-center h-3.5 w-3.5 rounded-full bg-red-500 hover:bg-red-600 text-white cursor-pointer"
                                      title="Remover este link"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setDeleteLinkTarget(link);
                                        setDeleteLinkDialogOpen(true);
                                      }}
                                    >
                                      <X className="h-2 w-2" />
                                    </button>
                                    <span className="text-xs font-bold">{link.stock ?? 0}</span>
                                    <span className="text-[9px] text-muted-foreground leading-none">{link.integrationName || link.externalId}</span>
                                  </div>
                                ))}
                              </div>
                            );
                          }
                          // Show link button if there are TikTok integrations
                          const tiktokIntegs = connectedMarketplace.filter(i => i.platform === "tiktok");
                          if (tiktokIntegs.length > 0) {
                            return (
                              <button
                                className="text-[10px] text-blue-500 hover:text-blue-700 hover:underline cursor-pointer"
                                title="Vincular manualmente ao TikTok"
                                onClick={() => {
                                  setManualLinkProduct(product);
                                  setManualLinkDialogOpen(true);
                                }}
                              >
                                + vincular
                              </button>
                            );
                          }
                          return <span className="text-[10px] text-muted-foreground">—</span>;
                        })()}
                      </TableCell>
                      <TableCell className="text-center py-1.5">
                        {isLowStock ? (
                          <Badge variant="outline" className="text-yellow-600 border-yellow-200 bg-yellow-50 gap-0.5 text-[10px] px-1.5 py-0">
                            <AlertTriangle className="h-2.5 w-2.5" />
                            Baixo
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-emerald-600 border-emerald-200 bg-emerald-50 text-[10px] px-1.5 py-0">
                            OK
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Popover open={syncProductId === product.id} onOpenChange={(open) => {
                            if (open) {
                              setSyncProductId(product.id);
                              const linked = new Set<number>();
                              if (product.shopeeIntegrationId) linked.add(product.shopeeIntegrationId);
                              if (product.amazonIntegrationId) linked.add(product.amazonIntegrationId);
                              if (product.mercadolivreIntegrationId) linked.add(product.mercadolivreIntegrationId);
                              // Include all product_links (ML, Shopee, TikTok, etc.)
                              const allLinks = productLinks.filter(l => l.productId === product.id);
                              allLinks.forEach(l => linked.add(l.integrationId));
                              setSyncSelectedIds(linked);
                            } else {
                              setSyncProductId(null);
                              setSyncSelectedIds(new Set());
                            }
                          }}>
                            <PopoverTrigger asChild>
                              <Button size="sm" variant="ghost" className="text-cyan-600 hover:text-cyan-700 hover:bg-cyan-50" title="Sincronizar este SKU">
                                <RefreshCw className="h-3.5 w-3.5" />
                              </Button>
                            </PopoverTrigger>
                            <PopoverContent className="w-72 p-3" align="end">
                              <div className="space-y-3">
                                <div>
                                  <p className="text-sm font-semibold">Sincronizar: {product.sku}</p>
                                  <p className="text-xs text-muted-foreground">Selecione as contas para sincronizar</p>
                                </div>
                                <div className="space-y-2 max-h-48 overflow-y-auto">
                                  {marketplaceIntegrations.filter(i => i.status === "connected" || i.status === "error").map(integ => (
                                    <label key={integ.id} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-accent/50 rounded px-1 py-0.5">
                                      <Checkbox
                                        checked={syncSelectedIds.has(integ.id)}
                                        onCheckedChange={(checked) => {
                                          setSyncSelectedIds(prev => {
                                            const next = new Set(prev);
                                            if (checked) next.add(integ.id); else next.delete(integ.id);
                                            return next;
                                          });
                                        }}
                                      />
                                      <PlatformBadge platform={integ.platform as any} />
                                      <span className="truncate">{integ.name}</span>
                                    </label>
                                  ))}
                                </div>
                                <div className="flex items-center justify-between gap-2">
                                  <span className="text-xs text-muted-foreground">{syncSelectedIds.size} selecionada(s)</span>
                                  <Button
                                    size="sm"
                                    className="gap-1"
                                    disabled={syncSelectedIds.size === 0 || syncProductSelectedMutation.isPending}
                                    onClick={() => {
                                      syncProductSelectedMutation.mutate({
                                        productId: product.id,
                                        integrationIds: Array.from(syncSelectedIds),
                                      });
                                    }}
                                  >
                                    {syncProductSelectedMutation.isPending ? (
                                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    ) : (
                                      <Play className="h-3.5 w-3.5" />
                                    )}
                                    Sincronizar
                                  </Button>
                                </div>
                              </div>
                            </PopoverContent>
                          </Popover>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-red-600 hover:text-red-700 hover:bg-red-50"
                            onClick={() => deleteMutation.mutate({ id: product.id })}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>

            {/* Pagination Controls */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-border/30">
                <p className="text-sm text-muted-foreground">
                  Mostrando {((currentPage - 1) * ITEMS_PER_PAGE) + 1}–{Math.min(currentPage * ITEMS_PER_PAGE, filtered.length)} de {filtered.length} produtos
                </p>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0"
                    onClick={() => setCurrentPage(1)}
                    disabled={currentPage === 1}
                  >
                    <ChevronsLeft className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0"
                    onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                    disabled={currentPage === 1}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <div className="flex items-center gap-1 mx-2">
                    {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                      let page: number;
                      if (totalPages <= 5) {
                        page = i + 1;
                      } else if (currentPage <= 3) {
                        page = i + 1;
                      } else if (currentPage >= totalPages - 2) {
                        page = totalPages - 4 + i;
                      } else {
                        page = currentPage - 2 + i;
                      }
                      return (
                        <Button
                          key={page}
                          variant={currentPage === page ? "default" : "ghost"}
                          size="sm"
                          className={`h-8 w-8 p-0 text-xs ${currentPage === page ? 'bg-cyan-600 hover:bg-cyan-700 text-white' : ''}`}
                          onClick={() => setCurrentPage(page)}
                        >
                          {page}
                        </Button>
                      );
                    })}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0"
                    onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                    disabled={currentPage === totalPages}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0"
                    onClick={() => setCurrentPage(totalPages)}
                    disabled={currentPage === totalPages}
                  >
                    <ChevronsRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            )}
          </Card>
        )}

        {/* Floating Action Bar for Selected Products */}
        {selectedProductIds.size > 0 && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-4 bg-card border border-cyan-200 rounded-xl px-6 py-3 shadow-2xl shadow-cyan-100">
            <div className="flex items-center gap-2">
              <CheckSquare className="h-4 w-4 text-cyan-600" />
              <span className="text-sm font-medium">
                {selectedProductIds.size} produto(s) selecionado(s)
              </span>
            </div>
            <div className="h-6 w-px bg-border" />
            <Button
              size="sm"
              className="gap-2 bg-cyan-600 hover:bg-cyan-700 text-white"
              onClick={() => {
                // Filter integrations to only those linked to selected products
                const linkedIntegIds = new Set<number>();
                Array.from(selectedProductIds).forEach(pid => {
                  const p = products.find(pr => pr.id === pid);
                  if (p) {
                    if (p.shopeeIntegrationId) linkedIntegIds.add(p.shopeeIntegrationId);
                    if (p.amazonIntegrationId) linkedIntegIds.add(p.amazonIntegrationId);
                    if (p.mercadolivreIntegrationId) linkedIntegIds.add(p.mercadolivreIntegrationId);
                  }
                  productLinks.filter(l => l.productId === pid).forEach(l => linkedIntegIds.add(l.integrationId));
                });
                const relevantIntegs = connectedMarketplace.filter(i => linkedIntegIds.has(i.id));
                setSelectedSyncSelectedIntegIds(new Set(relevantIntegs.map(i => i.id)));
                setIsSyncSelectedDialogOpen(true);
              }}
              disabled={isSyncing}
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Sincronizar Selecionados
            </Button>
            <Button
              size="sm"
              variant="destructive"
              className="gap-2"
              onClick={() => setIsBulkDeleteDialogOpen(true)}
              disabled={bulkDeleteMutation.isPending}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Excluir Selecionados
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => setSelectedProductIds(new Set())}
            >
              <X className="h-3.5 w-3.5" />
              Limpar
            </Button>
          </div>
        )}

        {/* Bulk Delete Confirmation Dialog */}
        <AlertDialog open={isBulkDeleteDialogOpen} onOpenChange={setIsBulkDeleteDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle className="flex items-center gap-2 text-red-600">
                <Trash2 className="h-5 w-5" />
                Excluir {selectedProductIds.size} produto(s)?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Esta ação não pode ser desfeita. Os {selectedProductIds.size} produtos selecionados serão removidos permanentemente do sistema, incluindo todos os vínculos com marketplaces.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="rounded-lg border border-border/30 bg-muted/20 p-3">
              <p className="text-xs text-muted-foreground mb-2">Produtos que serão excluídos:</p>
              <div className="flex flex-wrap gap-1.5 max-h-[100px] overflow-y-auto">
                {Array.from(selectedProductIds).slice(0, 30).map(pid => {
                  const p = products.find(pr => pr.id === pid);
                  return p ? (
                    <Badge key={pid} variant="outline" className="text-xs font-mono text-red-600 border-red-200">
                      {p.sku}
                    </Badge>
                  ) : null;
                })}
                {selectedProductIds.size > 30 && (
                  <Badge variant="outline" className="text-xs text-red-600 border-red-200">+{selectedProductIds.size - 30} mais</Badge>
                )}
              </div>
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancelar</AlertDialogCancel>
              <AlertDialogAction
                className="bg-red-600 hover:bg-red-700 text-white"
                onClick={() => {
                  bulkDeleteMutation.mutate({ ids: Array.from(selectedProductIds) });
                  setIsBulkDeleteDialogOpen(false);
                }}
              >
                {bulkDeleteMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 animate-spin mr-2" />Excluindo...</>
                ) : (
                  <>Excluir {selectedProductIds.size} produto(s)</>
                )}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* Delete Individual Link Dialog */}
        <AlertDialog open={deleteLinkDialogOpen} onOpenChange={setDeleteLinkDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle className="flex items-center gap-2 text-red-600">
                <Trash2 className="h-5 w-5" />
                Remover link?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Tem certeza que deseja remover o link <strong>{deleteLinkTarget?.integrationName || deleteLinkTarget?.externalId}</strong>
                {deleteLinkTarget?.platform && ` (${deleteLinkTarget.platform})`}?
                O produto não será mais sincronizado com este marketplace. Esta ação não pode ser desfeita.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancelar</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => deleteLinkTarget && deleteLinkMutation.mutate({ linkId: deleteLinkTarget.id })}
                className="bg-red-500 hover:bg-red-600 text-white"
                disabled={deleteLinkMutation.isPending}
              >
                {deleteLinkMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 animate-spin mr-1" /> Removendo...</>
                ) : (
                  "Remover"
                )}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* Sync Selected Products Dialog */}
        <Dialog open={isSyncSelectedDialogOpen} onOpenChange={(open) => {
          setIsSyncSelectedDialogOpen(open);
          if (!open && !isSyncing) {
            setSelectedSyncSelectedIntegIds(new Set());
          }
        }}>
          <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <RefreshCw className={`h-5 w-5 text-cyan-600 ${isSyncing ? 'animate-spin' : ''}`} />
                Sincronizar {selectedProductIds.size} Produto(s) Selecionado(s)
              </DialogTitle>
              <DialogDescription>
                {isSyncing
                  ? "Sincroniza\u00e7\u00e3o em andamento. Voc\u00ea pode fechar este dialog \u2014 o processo continua em segundo plano."
                  : "Selecione as contas/plataformas para sincronizar os produtos selecionados."
                }
              </DialogDescription>
            </DialogHeader>

            {/* Progress section - reuses the same syncProgress state */}
            {isSyncing && syncProgress && (
              <div className="overflow-y-auto space-y-4 py-2 min-h-0">
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">
                      {syncProgress.currentSku
                        ? `Sincronizando: ${syncProgress.currentSku}`
                        : "Iniciando sincroniza\u00e7\u00e3o..."}
                    </span>
                    <span className="font-mono font-semibold text-cyan-600">
                      {syncProgressPercent}%
                    </span>
                  </div>
                  <Progress value={syncProgressPercent} className="h-2.5" />
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>{syncProgress.processed} / {syncProgress.total} produtos</span>
                    <span className="flex gap-3">
                       <span className="text-emerald-600">✓ {syncProgress.synced}</span>
                       {((syncProgress as any).skippedVerified ?? 0) > 0 && <span className="text-blue-600">✔ {(syncProgress as any).skippedVerified}</span>}
                       {((syncProgress as any).skippedClosed ?? 0) > 0 && <span className="text-yellow-600">⊘ {(syncProgress as any).skippedClosed}</span>}
                       {syncProgress.errors > 0 && <span className="text-red-600">✗ {syncProgress.errors}</span>}
                     </span>
                  </div>
                </div>
                {syncProgress.details && syncProgress.details.length > 0 && (
                  <ScrollArea className="max-h-[150px] rounded-lg border border-border/30 bg-muted/20 p-2">
                    <div className="space-y-0.5">
                      {syncProgress.details.slice(-10).reverse().map((detail: string, idx: number) => (
                        <p key={idx} className={`text-xs font-mono ${
                          detail.startsWith('✓') ? 'text-emerald-600/80' :
                          detail.startsWith('⊘') ? 'text-yellow-600/80' :
                          detail.startsWith('✗') ? 'text-red-600/80' :
                          'text-muted-foreground'
                        }`}>
                          {detail}
                        </p>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </div>
            )}

            {/* Completed summary */}
            {syncJobDone && syncProgress && isSyncSelectedDialogOpen && (
              <div className="overflow-y-auto space-y-3 py-2 min-h-0">
                <div className={`text-center py-3 rounded-lg ${
                  syncProgress.status === 'completed' && syncProgress.errors === 0
                    ? 'bg-emerald-50 border border-emerald-200'
                    : syncProgress.status === 'completed'
                    ? 'bg-amber-50 border border-amber-200'
                    : 'bg-red-50 border border-red-200'
                }`}>
                  <div className={`text-2xl font-bold ${
                    syncProgress.status === 'completed' && syncProgress.errors === 0
                      ? 'text-emerald-600'
                      : syncProgress.status === 'completed'
                      ? 'text-amber-600'
                      : 'text-red-600'
                  }`}>
                    {syncProgress.status === 'completed'
                      ? `${syncProgress.synced} sincronizado(s)`
                      : 'Erro na sincroniza\u00e7\u00e3o'}
                  </div>
                  <div className="text-sm text-muted-foreground mt-1">
                    {syncProgress.processed} processados | {syncProgress.errors} erro(s)
                  </div>
                </div>
              </div>
            )}

            {/* Account selection */}
            {!isSyncing && !syncJobDone && (
              <div className="overflow-y-auto space-y-4 py-2 min-h-0">
                <div className="rounded-lg border border-border/30 bg-muted/20 p-3 flex-shrink-0">
                  <p className="text-xs text-muted-foreground mb-2">Produtos selecionados:</p>
                  <div className="flex flex-wrap gap-1.5 max-h-[80px] overflow-y-auto">
                    {Array.from(selectedProductIds).slice(0, 20).map(pid => {
                      const p = products.find(pr => pr.id === pid);
                      return p ? (
                        <Badge key={pid} variant="outline" className="text-xs font-mono">
                          {p.sku}
                        </Badge>
                      ) : null;
                    })}
                    {selectedProductIds.size > 20 && (
                      <Badge variant="outline" className="text-xs">+{selectedProductIds.size - 20} mais</Badge>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm font-medium">Selecione as contas:</Label>
                    <div className="flex gap-2">
                      <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={() => {
                        const linkedIntegIds = new Set<number>();
                        Array.from(selectedProductIds).forEach(pid => {
                          const p = products.find(pr => pr.id === pid);
                          if (p) {
                            if (p.shopeeIntegrationId) linkedIntegIds.add(p.shopeeIntegrationId);
                            if (p.amazonIntegrationId) linkedIntegIds.add(p.amazonIntegrationId);
                            if (p.mercadolivreIntegrationId) linkedIntegIds.add(p.mercadolivreIntegrationId);
                          }
                          productLinks.filter(l => l.productId === pid).forEach(l => linkedIntegIds.add(l.integrationId));
                        });
                        setSelectedSyncSelectedIntegIds(new Set(connectedMarketplace.filter(i => linkedIntegIds.has(i.id)).map(i => i.id)));
                      }}>
                        Todas
                      </Button>
                      <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={() => setSelectedSyncSelectedIntegIds(new Set())}>
                        Nenhuma
                      </Button>
                    </div>
                  </div>
                  <ScrollArea className="max-h-[200px] rounded-lg border border-border/50 p-2">
                    <div className="space-y-1">
                      {(() => {
                        // Filter to only show integrations linked to selected products
                        const linkedIntegIds = new Set<number>();
                        Array.from(selectedProductIds).forEach(pid => {
                          const p = products.find(pr => pr.id === pid);
                          if (p) {
                            if (p.shopeeIntegrationId) linkedIntegIds.add(p.shopeeIntegrationId);
                            if (p.amazonIntegrationId) linkedIntegIds.add(p.amazonIntegrationId);
                            if (p.mercadolivreIntegrationId) linkedIntegIds.add(p.mercadolivreIntegrationId);
                          }
                          productLinks.filter(l => l.productId === pid).forEach(l => linkedIntegIds.add(l.integrationId));
                        });
                        const relevantIntegs = connectedMarketplace.filter(i => linkedIntegIds.has(i.id));
                        return relevantIntegs.length === 0 ? (
                          <p className="text-sm text-muted-foreground text-center py-4">Nenhuma conta vinculada aos produtos selecionados.</p>
                        ) : (
                          relevantIntegs.map(integ => (
                            <label key={integ.id} className="flex items-center gap-3 rounded-md px-3 py-2 hover:bg-muted/50 cursor-pointer transition-colors">
                              <Checkbox
                                checked={selectedSyncSelectedIntegIds.has(integ.id)}
                                onCheckedChange={(checked) => {
                                  const next = new Set(selectedSyncSelectedIntegIds);
                                  if (checked) next.add(integ.id); else next.delete(integ.id);
                                  setSelectedSyncSelectedIntegIds(next);
                                }}
                              />
                              <PlatformBadge platform={integ.platform as any} />
                              <span className="text-sm flex-1">{integ.name}</span>
                            </label>
                          ))
                        );
                      })()}
                    </div>
                  </ScrollArea>
                  {(() => {
                    const linkedIntegIds = new Set<number>();
                    Array.from(selectedProductIds).forEach(pid => {
                      const p = products.find(pr => pr.id === pid);
                      if (p) {
                        if (p.shopeeIntegrationId) linkedIntegIds.add(p.shopeeIntegrationId);
                        if (p.amazonIntegrationId) linkedIntegIds.add(p.amazonIntegrationId);
                        if (p.mercadolivreIntegrationId) linkedIntegIds.add(p.mercadolivreIntegrationId);
                      }
                      productLinks.filter(l => l.productId === pid).forEach(l => linkedIntegIds.add(l.integrationId));
                    });
                    const relevantCount = connectedMarketplace.filter(i => linkedIntegIds.has(i.id)).length;
                    return relevantCount > 0 ? (
                      <p className="text-xs text-muted-foreground">
                        {selectedSyncSelectedIntegIds.size} de {relevantCount} conta(s) vinculada(s) selecionada(s)
                      </p>
                    ) : null;
                  })()}
                </div>

                <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-3 flex-shrink-0">
                  <p className="text-xs text-cyan-600">
                    <strong>Info:</strong> Apenas os {selectedProductIds.size} produto(s) selecionados serão sincronizados. A sincronização roda em segundo plano.
                  </p>
                </div>
              </div>
            )}

            <DialogFooter className="border-t border-border/30 pt-3">
              {syncJobDone ? (
                <Button variant="outline" onClick={() => {
                  setIsSyncSelectedDialogOpen(false);
                  setSyncJobId(null);
                  setSyncJobDone(false);
                  setSelectedProductIds(new Set());
                  setSelectedSyncSelectedIntegIds(new Set());
                }}>Fechar</Button>
              ) : isSyncing ? (
                <Button variant="outline" onClick={() => setIsSyncSelectedDialogOpen(false)}>Minimizar</Button>
              ) : (
                <>
                  <Button variant="outline" onClick={() => setIsSyncSelectedDialogOpen(false)}>Cancelar</Button>
                  <Button
                    className="gap-2"
                    onClick={() => {
                      startSyncAllMutation.mutate({
                        integrationIds: selectedSyncSelectedIntegIds.size > 0 ? Array.from(selectedSyncSelectedIntegIds) : undefined,
                        productIds: Array.from(selectedProductIds),
                      });
                    }}
                    disabled={startSyncAllMutation.isPending || (connectedMarketplace.length > 0 && selectedSyncSelectedIntegIds.size === 0)}
                  >
                    {startSyncAllMutation.isPending ? (
                      <><Loader2 className="h-4 w-4 animate-spin" />Iniciando...</>
                    ) : (
                      <><RefreshCw className="h-4 w-4" />Sincronizar {selectedProductIds.size} produto(s)</>  
                    )}
                  </Button>
                </>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Create Product Dialog */}
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Novo Produto</DialogTitle>
              <DialogDescription>Mapeie o produto entre as plataformas usando seus IDs.</DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2 col-span-2">
                <Label>Nome do Produto *</Label>
                <Input placeholder="Ex: Camiseta Azul M" value={form.name} onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label>SKU *</Label>
                <Input placeholder="Ex: CAM-AZL-M" value={form.sku} onChange={e => setForm(p => ({ ...p, sku: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label>Estoque Mínimo</Label>
                <Input type="number" placeholder="5" value={form.lowStockThreshold} onChange={e => setForm(p => ({ ...p, lowStockThreshold: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label className="flex items-center gap-2"><PlatformBadge platform="bling" />ID no Bling</Label>
                <Input placeholder="ID do produto no Bling" value={form.blingId} onChange={e => setForm(p => ({ ...p, blingId: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label className="flex items-center gap-2"><PlatformBadge platform="shopee" />ID na Shopee</Label>
                <Input placeholder="Item ID da Shopee" value={form.shopeeId} onChange={e => setForm(p => ({ ...p, shopeeId: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label className="flex items-center gap-2"><PlatformBadge platform="amazon" />SKU na Amazon</Label>
                <Input placeholder="Seller SKU da Amazon" value={form.amazonId} onChange={e => setForm(p => ({ ...p, amazonId: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label className="flex items-center gap-2"><PlatformBadge platform="mercadolivre" />ID no ML</Label>
                <Input placeholder="Ex: MLB123456789" value={form.mercadolivreId} onChange={e => setForm(p => ({ ...p, mercadolivreId: e.target.value }))} />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsDialogOpen(false)}>Cancelar</Button>
              <Button onClick={handleCreate} disabled={createMutation.isPending}>
                {createMutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                Salvar Produto
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* CSV Import Dialog */}
        <Dialog open={isCsvDialogOpen} onOpenChange={(open) => { setIsCsvDialogOpen(open); if (!open) { setCsvFile(null); setCsvPreview([]); setCsvError(null); } }}>
          <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <FileUp className="h-5 w-5" />
                Importar Produtos via CSV
              </DialogTitle>
              <DialogDescription>
                Importe centenas de produtos de uma vez. Baixe o template, preencha e envie.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4">
              {/* Template Download */}
              <Card className="border-border/40 bg-muted/20">
                <CardContent className="flex items-center justify-between py-4">
                  <div>
                    <p className="text-sm font-medium">Template CSV</p>
                    <p className="text-xs text-muted-foreground">Colunas: sku, nome, id_bling, id_shopee, id_amazon, id_mercadolivre, estoque_minimo</p>
                  </div>
                  <Button variant="outline" size="sm" className="gap-2" onClick={downloadCsvTemplate}>
                    <Download className="h-4 w-4" />
                    Baixar Template
                  </Button>
                </CardContent>
              </Card>

              {/* File Upload */}
              <div
                className="border-2 border-dashed border-border/50 rounded-lg p-8 text-center cursor-pointer hover:border-primary/50 hover:bg-primary/5 transition-colors"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const file = e.dataTransfer.files[0];
                  if (file) {
                    const fakeEvent = { target: { files: [file] } } as any;
                    handleFileChange(fakeEvent);
                  }
                }}
              >
                <input ref={fileInputRef} type="file" accept=".csv,.txt" className="hidden" onChange={handleFileChange} />
                {csvFile ? (
                  <div className="flex items-center justify-center gap-3">
                    <FileUp className="h-8 w-8 text-primary" />
                    <div className="text-left">
                      <p className="font-medium text-sm">{csvFile.name}</p>
                      <p className="text-xs text-muted-foreground">{(csvFile.size / 1024).toFixed(1)} KB</p>
                    </div>
                    <Button size="sm" variant="ghost" className="ml-2" onClick={(e) => { e.stopPropagation(); setCsvFile(null); setCsvPreview([]); setCsvError(null); }}>
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ) : (
                  <>
                    <Upload className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
                    <p className="text-sm font-medium">Arraste o arquivo CSV aqui ou clique para selecionar</p>
                    <p className="text-xs text-muted-foreground mt-1">Suporta arquivos .csv e .txt</p>
                  </>
                )}
              </div>

              {/* Error */}
              {csvError && (
                <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg p-3">
                  <AlertTriangle className="h-4 w-4 flex-shrink-0" />
                  {csvError}
                </div>
              )}

              {/* Preview */}
              {csvPreview.length > 0 && (
                <Card className="border-border/40">
                  <CardHeader className="pb-2 pt-4 px-4">
                    <CardTitle className="text-sm font-medium text-muted-foreground">
                      Pré-visualização — {csvPreview.length} produto(s) encontrado(s)
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="px-0 pb-0">
                    <div className="max-h-48 overflow-y-auto">
                      <Table>
                        <TableHeader>
                          <TableRow className="border-border/30 hover:bg-transparent">
                            <TableHead className="text-xs">SKU</TableHead>
                            <TableHead className="text-xs">Nome</TableHead>
                            <TableHead className="text-xs text-center">Bling</TableHead>
                            <TableHead className="text-xs text-center">Shopee</TableHead>
                            <TableHead className="text-xs text-center">Amazon</TableHead>
                            <TableHead className="text-xs text-center">ML</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {csvPreview.slice(0, 10).map((row, i) => (
                            <TableRow key={i} className="border-border/20">
                              <TableCell className="text-xs font-mono py-2">{row.sku}</TableCell>
                              <TableCell className="text-xs py-2">{row.name}</TableCell>
                              <TableCell className="text-xs text-center py-2">{row.blingId || "—"}</TableCell>
                              <TableCell className="text-xs text-center py-2">{row.shopeeId || "—"}</TableCell>
                              <TableCell className="text-xs text-center py-2">{row.amazonId || "—"}</TableCell>
                              <TableCell className="text-xs text-center py-2">{row.mercadolivreId || "—"}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                    {csvPreview.length > 10 && (
                      <p className="text-xs text-muted-foreground text-center py-2">
                        + {csvPreview.length - 10} produto(s) não exibido(s)
                      </p>
                    )}
                  </CardContent>
                </Card>
              )}

              {importCsvMutation.isPending && (
                <div className="space-y-2">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Importando produtos...</span>
                  </div>
                  <Progress value={undefined} className="h-1.5" />
                </div>
              )}
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={() => setIsCsvDialogOpen(false)}>Cancelar</Button>
              <Button
                onClick={handleImportCsv}
                disabled={csvPreview.length === 0 || importCsvMutation.isPending || !!csvError}
                className="gap-2"
              >
                {importCsvMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 animate-spin" />Importando...</>
                ) : (
                  <><Upload className="h-4 w-4" />Importar {csvPreview.length > 0 ? `${csvPreview.length} Produtos` : ""}</>
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {/* Bling Auto-Import Dialog */}
        <Dialog open={isBlingDialogOpen} onOpenChange={(open) => { setIsBlingDialogOpen(open); if (!open) { setSelectedBlingIds(new Set()); setBlingSearchQuery(""); } }}>
          <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col overflow-hidden">
            <DialogHeader className="flex-shrink-0 pb-3 border-b border-border/30">
              <DialogTitle className="flex items-center gap-2">
                <Zap className="h-5 w-5 text-orange-600" />
                Importar Produtos do Bling
              </DialogTitle>
              <DialogDescription>
                Selecione quais produtos deseja importar.
              </DialogDescription>
            </DialogHeader>

            <div className="flex-1 overflow-hidden flex flex-col space-y-3 pt-3">
              {blingProductsQuery.isLoading && (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <Loader2 className="h-8 w-8 animate-spin text-orange-600" />
                  <p className="text-sm text-muted-foreground">Buscando produtos no Bling...</p>
                  <p className="text-xs text-muted-foreground">Isso pode levar alguns segundos para catálogos grandes</p>
                </div>
              )}

              {blingProductsQuery.isError && (
                <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 border border-red-200 rounded-lg p-4">
                  <AlertTriangle className="h-5 w-5 flex-shrink-0" />
                  <div>
                    <p className="font-medium">Erro ao buscar produtos do Bling</p>
                    <p className="text-xs mt-1">{blingProductsQuery.error?.message}</p>
                  </div>
                </div>
              )}

              {blingProductsQuery.data && (() => {
                const allProducts = blingProductsQuery.data;
                const filteredBling = allProducts.filter(p =>
                  p.name.toLowerCase().includes(blingSearchQuery.toLowerCase()) ||
                  p.sku.toLowerCase().includes(blingSearchQuery.toLowerCase())
                );
                const newCount = allProducts.filter(p => !p.alreadyImported).length;
                const allNewSelected = filteredBling.filter(p => !p.alreadyImported).every(p => selectedBlingIds.has(p.blingId));

                return (
                  <>
                    {/* Stats bar */}
                    <div className="flex-shrink-0 grid grid-cols-4 gap-2 text-xs text-center">
                      <div className="bg-muted/20 rounded-lg px-2 py-2">
                        <div className="text-foreground font-semibold text-sm">{allProducts.length}</div>
                        <div className="text-muted-foreground">no Bling</div>
                      </div>
                      <div className="bg-muted/20 rounded-lg px-2 py-2">
                        <div className="text-emerald-600 font-semibold text-sm">{allProducts.length - newCount}</div>
                        <div className="text-muted-foreground">importado(s)</div>
                      </div>
                      <div className="bg-muted/20 rounded-lg px-2 py-2">
                        <div className="text-orange-600 font-semibold text-sm">{newCount}</div>
                        <div className="text-muted-foreground">novo(s)</div>
                      </div>
                      <div className="bg-muted/20 rounded-lg px-2 py-2">
                        <div className="text-primary font-semibold text-sm">{selectedBlingIds.size}</div>
                        <div className="text-muted-foreground">selecionado(s)</div>
                      </div>
                    </div>

                    {/* Search + Select All */}
                    <div className="flex-shrink-0 flex gap-2">
                      <div className="relative flex-1">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                        <Input placeholder="Buscar por nome ou SKU..." className="pl-9 h-9" value={blingSearchQuery} onChange={e => setBlingSearchQuery(e.target.value)} />
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-2 text-xs whitespace-nowrap"
                        onClick={() => {
                          const newIds = filteredBling.filter(p => !p.alreadyImported).map(p => p.blingId);
                          if (allNewSelected) {
                            setSelectedBlingIds(prev => { const next = new Set(prev); newIds.forEach(id => next.delete(id)); return next; });
                          } else {
                            setSelectedBlingIds(prev => { const next = new Set(prev); newIds.forEach(id => next.add(id)); return next; });
                          }
                        }}
                      >
                        {allNewSelected ? <CheckSquare className="h-3.5 w-3.5" /> : <Square className="h-3.5 w-3.5" />}
                        {allNewSelected ? "Desmarcar" : "Selecionar Novos"}
                      </Button>
                    </div>

                    {/* Products list */}
                    <div className="flex-1 overflow-y-auto border border-border/30 rounded-lg">
                      <Table>
                        <TableHeader>
                          <TableRow className="border-border/30 hover:bg-transparent sticky top-0 bg-background">
                            <TableHead className="w-10"></TableHead>
                            <TableHead className="text-xs">SKU</TableHead>
                            <TableHead className="text-xs">Nome</TableHead>
                            <TableHead className="text-xs text-center">Estoque</TableHead>
                            <TableHead className="text-xs text-center">Status</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {filteredBling.map(product => (
                            <TableRow
                              key={product.blingId}
                              className={`border-border/20 cursor-pointer ${
                                product.alreadyImported ? "opacity-50" : "hover:bg-muted/20"
                              }`}
                              onClick={() => {
                                if (product.alreadyImported) return;
                                setSelectedBlingIds(prev => {
                                  const next = new Set(prev);
                                  if (next.has(product.blingId)) next.delete(product.blingId);
                                  else next.add(product.blingId);
                                  return next;
                                });
                              }}
                            >
                              <TableCell className="py-2">
                                <Checkbox
                                  checked={selectedBlingIds.has(product.blingId)}
                                  disabled={product.alreadyImported}
                                  onCheckedChange={() => {}}
                                  className="pointer-events-none"
                                />
                              </TableCell>
                              <TableCell className="text-xs font-mono py-2">{product.sku}</TableCell>
                              <TableCell className="text-xs py-2 max-w-[200px] truncate">{product.name}</TableCell>
                              <TableCell className="text-xs text-center py-2 font-semibold">{product.stock}</TableCell>
                              <TableCell className="text-xs text-center py-2">
                                {product.alreadyImported ? (
                                  <Badge variant="outline" className="text-emerald-600 border-emerald-200 bg-emerald-50 text-[10px] py-0">
                                    Importado
                                  </Badge>
                                ) : (
                                  <Badge variant="outline" className="text-orange-600 border-orange-200 bg-orange-50 text-[10px] py-0">
                                    Novo
                                  </Badge>
                                )}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </>
                );
              })()}
            </div>

            {importFromBlingMutation.isPending && (
              <div className="space-y-1.5">
                <p className="text-xs text-muted-foreground">Importando produtos...</p>
                <Progress value={undefined} className="h-1.5" />
              </div>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={() => setIsBlingDialogOpen(false)}>Cancelar</Button>
              <Button
                onClick={() => importFromBlingMutation.mutate({ selectedIds: Array.from(selectedBlingIds) })}
                disabled={selectedBlingIds.size === 0 || importFromBlingMutation.isPending}
                className="gap-2 bg-orange-500 hover:bg-orange-600 text-white"
              >
                {importFromBlingMutation.isPending ? (
                  <><Loader2 className="h-4 w-4 animate-spin" />Importando...</>
                ) : (
                  <><Zap className="h-4 w-4" />Importar {selectedBlingIds.size > 0 ? `${selectedBlingIds.size} Produtos` : ""}</>
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Auto-Link Marketplaces Dialog */}
        <Dialog open={isAutoLinkDialogOpen} onOpenChange={(open) => { setIsAutoLinkDialogOpen(open); if (!open) setSelectedAutoLinkIds(new Set()); }}>
          <DialogContent className="max-w-lg flex flex-col max-h-[80vh] overflow-hidden">
            <DialogHeader className="flex-shrink-0">
              <DialogTitle className="flex items-center gap-2">
                <Zap className="h-5 w-5 text-emerald-600" />
                Vincular Anúncios Automaticamente
              </DialogTitle>
              <DialogDescription>
                Selecione as contas que deseja vincular e a plataforma vai buscar os anúncios e vincular automaticamente pelo SKU.
              </DialogDescription>
            </DialogHeader>

            {!autoLinkResult ? (
              <>
                {/* Scrollable content area */}
                <div className="flex-1 overflow-y-auto min-h-0 space-y-4 py-2 pr-1">
                  {/* Integration Selection */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="text-sm font-medium">Selecione as contas para vincular:</Label>
                      <div className="flex gap-2">
                        <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={() => setSelectedAutoLinkIds(new Set(connectedMarketplace.map(i => i.id)))}>
                          Todas
                        </Button>
                        <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={() => setSelectedAutoLinkIds(new Set())}>
                          Nenhuma
                        </Button>
                      </div>
                    </div>
                    <div className="rounded-lg border border-border/50 max-h-[280px] overflow-y-auto">
                      {connectedMarketplace.length === 0 ? (
                        <p className="text-sm text-muted-foreground text-center py-4">Nenhuma integração de marketplace conectada.</p>
                      ) : (
                        connectedMarketplace.map((integ, idx) => (
                          <label
                            key={integ.id}
                            className={`flex items-center gap-3 px-3 py-2.5 hover:bg-muted/50 cursor-pointer transition-colors ${idx !== connectedMarketplace.length - 1 ? 'border-b border-border/30' : ''}`}
                          >
                            <Checkbox
                              checked={selectedAutoLinkIds.has(integ.id)}
                              onCheckedChange={(checked) => {
                                const next = new Set(selectedAutoLinkIds);
                                if (checked) next.add(integ.id); else next.delete(integ.id);
                                setSelectedAutoLinkIds(next);
                              }}
                            />
                            <PlatformBadge platform={integ.platform as any} />
                            <span className="text-sm truncate">{integ.name}</span>
                          </label>
                        ))
                      )}
                    </div>
                    {connectedMarketplace.length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        {selectedAutoLinkIds.size} de {connectedMarketplace.length} conta(s) selecionada(s)
                      </p>
                    )}
                  </div>

                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                    <p className="text-xs text-amber-600">
                      <strong>Importante:</strong> Os SKUs precisam ser idênticos entre o Bling e os marketplaces para a vinculação funcionar.
                    </p>
                  </div>

                  {/* Progress indicator while linking */}
                  {isAutoLinking && autoLinkProgress.data && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin text-emerald-600" />
                        <span>Vinculando... {autoLinkProgress.data.currentSku || ""}</span>
                      </div>
                      <Progress value={autoLinkProgress.data.total > 0 ? (autoLinkProgress.data.processed / autoLinkProgress.data.total) * 100 : 0} className="h-2" />
                      <p className="text-xs text-muted-foreground">
                        {autoLinkProgress.data.processed}/{autoLinkProgress.data.total} plataformas processadas • {autoLinkProgress.data.synced ?? 0} vinculados
                      </p>
                      <p className="text-xs text-muted-foreground/70">Você pode fechar este dialog — o processo continua em segundo plano.</p>
                    </div>
                  )}
                </div>

                {/* Fixed footer */}
                <DialogFooter className="flex-shrink-0 border-t border-border/30 pt-3">
                  <Button variant="outline" onClick={() => setIsAutoLinkDialogOpen(false)}>Cancelar</Button>
                  <Button
                    className="gap-2 bg-emerald-600 hover:bg-emerald-700 text-white"
                    onClick={() => startAutoLinkMutation.mutate({
                      integrationIds: selectedAutoLinkIds.size > 0 ? Array.from(selectedAutoLinkIds) : undefined,
                    })}
                    disabled={startAutoLinkMutation.isPending || isAutoLinking || (connectedMarketplace.length > 0 && selectedAutoLinkIds.size === 0)}
                  >
                    {(startAutoLinkMutation.isPending || isAutoLinking) ? (
                      <><Loader2 className="h-4 w-4 animate-spin" />Vinculando...</>
                    ) : (
                      <><Zap className="h-4 w-4" />Iniciar Vinculação ({selectedAutoLinkIds.size})</>
                    )}
                  </Button>
                </DialogFooter>
              </>
            ) : (
              <>
                {/* Result scrollable area */}
                <div className="flex-1 overflow-y-auto min-h-0 space-y-4 py-2 pr-1">
                  <div className="text-center py-2">
                    <div className="text-3xl font-bold text-emerald-600">{autoLinkResult.synced}</div>
                    <div className="text-sm text-muted-foreground">produto(s) vinculados com sucesso</div>
                  </div>
                  <div className="space-y-2">
                    {autoLinkResult.details.map((detail, idx) => (
                      <div key={idx} className="rounded-lg border border-border/50 p-3">
                        <p className="text-sm text-muted-foreground">{detail}</p>
                      </div>
                    ))}
                    {autoLinkResult.details.length === 0 && (
                      <div className="text-center text-sm text-muted-foreground py-4">
                        Nenhuma integração de marketplace conectada. Conecte Shopee, Amazon, TikTok ou Mercado Livre primeiro.
                      </div>
                    )}
                  </div>
                  {autoLinkResult.errors > 0 && (
                    <div className="rounded-lg border border-red-200 bg-red-50 p-3">
                      <p className="text-xs text-red-600">
                        <strong>{autoLinkResult.errors} erro(s)</strong> ocorreram durante a vinculação.
                      </p>
                    </div>
                  )}
                </div>

                {/* Fixed footer for results */}
                <DialogFooter className="flex-shrink-0 border-t border-border/30 pt-3">
                  <Button variant="outline" onClick={() => setIsAutoLinkDialogOpen(false)}>Fechar</Button>
                  {autoLinkResult.synced > 0 && (
                    <Button
                      className="gap-2"
                      onClick={() => { startSyncAllMutation.mutate(); setIsAutoLinkDialogOpen(false); }}
                      disabled={startSyncAllMutation.isPending || isSyncing}
                    >
                      <RefreshCw className="h-4 w-4" />
                      Sincronizar Estoque Agora
                    </Button>
                  )}
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* Sincronizar Todos Dialog */}
        <Dialog open={isSyncAllDialogOpen} onOpenChange={(open) => {
          setIsSyncAllDialogOpen(open);
          if (!open && !isSyncing) {
            setSelectedSyncAllIds(new Set());
          }
        }}>
          <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <RefreshCw className={`h-5 w-5 text-cyan-600 ${isSyncing ? 'animate-spin' : ''}`} />
                Sincronizar Todos os Produtos
              </DialogTitle>
              <DialogDescription>
                {isSyncing
                  ? "Sincronização em andamento. Você pode fechar este dialog — o processo continua em segundo plano."
                  : "Selecione as contas/plataformas que deseja sincronizar. O estoque do Bling será enviado para as plataformas selecionadas."
                }
              </DialogDescription>
            </DialogHeader>

            {/* Progress section - shown when syncing */}
            {isSyncing && syncProgress && (
              <div className="overflow-y-auto space-y-4 py-2 min-h-0">
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">
                      {syncProgress.currentSku
                        ? `Sincronizando: ${syncProgress.currentSku}`
                        : "Iniciando sincroniza\u00e7\u00e3o..."}
                    </span>
                    <span className="font-mono font-semibold text-cyan-600">
                      {syncProgressPercent}%
                    </span>
                  </div>
                  <Progress value={syncProgressPercent} className="h-2.5" />
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>{syncProgress.processed} / {syncProgress.total} produtos</span>
                    <span className="flex gap-3">
                      <span className="text-emerald-600">✓ {syncProgress.synced}</span>
                      {((syncProgress as any).skipped ?? 0) > 0 && <span className="text-yellow-600">⊘ {(syncProgress as any).skipped}</span>}
                      {syncProgress.errors > 0 && <span className="text-red-600">✗ {syncProgress.errors}</span>}
                    </span>
                  </div>
                </div>

                {/* Recent details */}
                {syncProgress.details && syncProgress.details.length > 0 && (
                  <ScrollArea className="max-h-[150px] rounded-lg border border-border/30 bg-muted/20 p-2">
                    <div className="space-y-0.5">
                      {syncProgress.details.slice(-10).reverse().map((detail: string, idx: number) => (
                        <p key={idx} className={`text-xs font-mono ${
                          detail.startsWith('✓') ? 'text-emerald-600/80' :
                          detail.startsWith('⊘') ? 'text-yellow-600/80' :
                          detail.startsWith('✗') ? 'text-red-600/80' :
                          'text-muted-foreground'
                        }`}>
                          {detail}
                        </p>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </div>
            )}

            {/* Completed/Error summary */}
            {syncJobDone && syncProgress && (
              <div className="overflow-y-auto space-y-3 py-2 min-h-0">
                <div className={`text-center py-3 rounded-lg ${
                  syncProgress.status === 'completed' && syncProgress.errors === 0
                    ? 'bg-emerald-50 border border-emerald-200'
                    : syncProgress.status === 'completed'
                    ? 'bg-amber-50 border border-amber-200'
                    : 'bg-red-50 border border-red-200'
                }`}>
                  <div className={`text-2xl font-bold ${
                    syncProgress.status === 'completed' && syncProgress.errors === 0
                      ? 'text-emerald-600'
                      : syncProgress.status === 'completed'
                      ? 'text-amber-600'
                      : 'text-red-600'
                  }`}>
                    {syncProgress.status === 'completed'
                      ? `${syncProgress.synced} sincronizado(s)`
                      : 'Erro na sincronização'}
                  </div>
                  <div className="text-sm text-muted-foreground mt-1">
                     {syncProgress.processed} processados
                     {((syncProgress as any).skippedVerified ?? 0) > 0 && <span className="text-blue-600 ml-1"> | {(syncProgress as any).skippedVerified} estoque igual</span>}
                     {((syncProgress as any).skippedClosed ?? 0) > 0 && <span className="text-yellow-600 ml-1"> | {(syncProgress as any).skippedClosed} fechado(s)</span>}
                     {syncProgress.errors > 0 && <span className="text-red-600 ml-1"> | {syncProgress.errors} erro(s)</span>}
                     {syncProgress.errors === 0 && ((syncProgress as any).skippedVerified ?? 0) === 0 && ((syncProgress as any).skippedClosed ?? 0) === 0 && <span> | 0 erro(s)</span>}
                  </div>
                </div>

                {syncProgress.details && syncProgress.details.length > 0 && (
                  <ScrollArea className="max-h-[350px] rounded-lg border border-border/30 bg-muted/20 p-2">
                    <div className="space-y-0.5">
                      {syncProgress.details.slice(-100).reverse().map((detail: string, idx: number) => (
                        <p key={idx} className={`text-xs font-mono ${
                          detail.startsWith('✓') ? 'text-emerald-600/80' :
                          detail.startsWith('⊘') ? 'text-yellow-600/80' :
                          detail.startsWith('✗') ? 'text-red-600/80' :
                          detail.includes('RESUMO FINAL') ? 'text-cyan-600 font-bold mt-2' :
                          detail.startsWith('✅') ? 'text-emerald-600 font-semibold' :
                          detail.startsWith('❌') ? 'text-red-600 font-semibold' :
                          detail.startsWith('⚠️') || detail.startsWith('⚠') ? 'text-yellow-600 font-semibold' :
                          detail.startsWith('  •') ? 'text-muted-foreground pl-2' :
                          'text-muted-foreground'
                        }`}>
                          {detail}
                        </p>
                      ))}
                    </div>
                  </ScrollArea>
                )}
              </div>
            )}

            {/* Account selection - shown when NOT syncing and NOT done */}
            {!isSyncing && !syncJobDone && (
              <div className="overflow-y-auto space-y-4 py-2 min-h-0">
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm font-medium">Selecione as contas:</Label>
                    <div className="flex gap-2">
                      <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={() => setSelectedSyncAllIds(new Set(connectedMarketplace.map(i => i.id)))}>
                        Todas
                      </Button>
                      <Button variant="ghost" size="sm" className="text-xs h-7 px-2" onClick={() => setSelectedSyncAllIds(new Set())}>
                        Nenhuma
                      </Button>
                    </div>
                  </div>
                  <ScrollArea className="max-h-[300px] rounded-lg border border-border/50 p-2">
                    <div className="space-y-1">
                      {connectedMarketplace.length === 0 ? (
                        <p className="text-sm text-muted-foreground text-center py-4">Nenhuma integração de marketplace conectada.</p>
                      ) : (
                        connectedMarketplace.map(integ => (
                          <label key={integ.id} className="flex items-center gap-3 rounded-md px-3 py-2 hover:bg-muted/50 cursor-pointer transition-colors">
                            <Checkbox
                              checked={selectedSyncAllIds.has(integ.id)}
                              onCheckedChange={(checked) => {
                                const next = new Set(selectedSyncAllIds);
                                if (checked) next.add(integ.id); else next.delete(integ.id);
                                setSelectedSyncAllIds(next);
                              }}
                            />
                            <PlatformBadge platform={integ.platform as any} />
                            <span className="text-sm flex-1">{integ.name}</span>
                          </label>
                        ))
                      )}
                    </div>
                  </ScrollArea>
                  {connectedMarketplace.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      {selectedSyncAllIds.size} de {connectedMarketplace.length} conta(s) selecionada(s)
                    </p>
                  )}
                </div>

                <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-3 flex-shrink-0">
                  <p className="text-xs text-cyan-600">
                    <strong>Info:</strong> A sincronização é executada em segundo plano. Você pode fechar este dialog e acompanhar o progresso a qualquer momento.
                  </p>
                </div>
              </div>
            )}

            <DialogFooter className="border-t border-border/30 pt-3">
              {syncJobDone ? (
                <Button variant="outline" onClick={() => {
                  setIsSyncAllDialogOpen(false);
                  setSyncJobId(null);
                  setSyncJobDone(false);
                  setSelectedSyncAllIds(new Set());
                }}>Fechar</Button>
              ) : isSyncing ? (
                <Button variant="outline" onClick={() => setIsSyncAllDialogOpen(false)}>Minimizar</Button>
              ) : (
                <>
                  <Button variant="outline" onClick={() => setIsSyncAllDialogOpen(false)}>Cancelar</Button>
                  <Button
                    className="gap-2"
                    onClick={() => startSyncAllMutation.mutate({
                      integrationIds: selectedSyncAllIds.size > 0 ? Array.from(selectedSyncAllIds) : undefined,
                    })}
                    disabled={startSyncAllMutation.isPending || (connectedMarketplace.length > 0 && selectedSyncAllIds.size === 0)}
                  >
                    {startSyncAllMutation.isPending ? (
                      <><Loader2 className="h-4 w-4 animate-spin" />Iniciando...</>
                    ) : (
                      <><RefreshCw className="h-4 w-4" />Sincronizar ({selectedSyncAllIds.size} contas)</>
                    )}
                  </Button>
                </>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* Manual Amazon Link Dialog */}
      <Dialog open={manualLinkDialogOpen} onOpenChange={(open) => {
        setManualLinkDialogOpen(open);
        if (!open) setManualLinkProduct(null);
      }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <PlatformBadge platform="amazon" />
              Vincular à Amazon
            </DialogTitle>
            <DialogDescription>
              Vincule o produto <strong>{manualLinkProduct?.sku}</strong> a uma conta Amazon. O SKU do produto será usado como identificador.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-2">
            {connectedMarketplace.filter(i => i.platform === "amazon").length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">Nenhuma conta Amazon conectada.</p>
            ) : (
              connectedMarketplace.filter(i => i.platform === "amazon").map(integ => (
                <div key={integ.id} className="flex items-center justify-between rounded-lg border border-border/50 p-3">
                  <div className="flex items-center gap-2">
                    <PlatformBadge platform="amazon" />
                    <span className="text-sm font-medium">{integ.name}</span>
                  </div>
                  <Button
                    size="sm"
                    className="gap-1.5"
                    disabled={manualLinkAmazonMutation.isPending}
                    onClick={() => {
                      if (manualLinkProduct) {
                        manualLinkAmazonMutation.mutate({
                          productId: manualLinkProduct.id,
                          integrationId: integ.id,
                        });
                      }
                    }}
                  >
                    {manualLinkAmazonMutation.isPending ? (
                      <><Loader2 className="h-3.5 w-3.5 animate-spin" />Vinculando...</>
                    ) : (
                      <><Plus className="h-3.5 w-3.5" />Vincular</>
                    )}
                  </Button>
                </div>
              ))
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setManualLinkDialogOpen(false)}>Fechar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </DashboardLayout>
  );
}
