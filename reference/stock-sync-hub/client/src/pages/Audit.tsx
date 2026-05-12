import { useState, useRef, useMemo, useEffect, useCallback } from "react";
import { trpc } from "@/lib/trpc";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import {
  Upload, FileSpreadsheet, Play, AlertTriangle, CheckCircle2,
  XCircle, Search, Filter, Download, BarChart3, ArrowUpDown,
  ChevronLeft, ChevronRight, Loader2, MinusCircle, Wrench, CheckCheck,
} from "lucide-react";

type AuditStep = "upload" | "configure" | "running" | "results";
type DivergenceFilter = "all" | "price_mismatch" | "missing" | "paused" | "ok";

interface AuditResultItem {
  productName: string;
  accountName: string;
  platform: string;
  listingType: string;
  expectedPrice: number;
  actualPrice: number | null;
  status: string | null;
  divergenceType: "price_mismatch" | "missing" | "paused" | "ok";
  priceDiff: number | null;
  externalId?: string;
  matchedTitle?: string;
  sku?: string;
}

const PLATFORM_COLORS: Record<string, string> = {
  mercadolivre: "bg-yellow-50 text-yellow-700 border-yellow-200",
  shopee: "bg-orange-50 text-orange-700 border-orange-200",
  amazon: "bg-blue-50 text-blue-700 border-blue-200",
  temu: "bg-red-50 text-red-700 border-red-200",
};

const PLATFORM_LABELS: Record<string, string> = {
  mercadolivre: "Mercado Livre",
  shopee: "Shopee",
  amazon: "Amazon",
  temu: "Temu",
};

const DIVERGENCE_CONFIG: Record<string, { label: string; color: string; icon: any }> = {
  ok: { label: "OK", color: "bg-emerald-50 text-emerald-700 border-emerald-200", icon: CheckCircle2 },
  price_mismatch: { label: "Preço Diferente", color: "bg-amber-50 text-amber-700 border-amber-200", icon: AlertTriangle },
  missing: { label: "Não Encontrado", color: "bg-red-50 text-red-700 border-red-200", icon: XCircle },
  paused: { label: "Pausado/Inativo", color: "bg-gray-100 text-gray-600 border-gray-200", icon: MinusCircle },
};

const PAGE_SIZE = 50;

export default function AuditPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);

  // State
  const [step, setStep] = useState<AuditStep>("upload");
  const [fileBase64, setFileBase64] = useState<string>("");
  const [fileName, setFileName] = useState<string>("");
  const [sheets, setSheets] = useState<string[]>([]);
  const [selectedSheet, setSelectedSheet] = useState<string>("");
  const [auditId, setAuditId] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [parseResult, setParseResult] = useState<any>(null);

  // Results state
  const [results, setResults] = useState<AuditResultItem[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [divergenceFilter, setDivergenceFilter] = useState<DivergenceFilter>("all");
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [currentPage, setCurrentPage] = useState(1);
  const [sortField, setSortField] = useState<string>("divergenceType");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // Fix price state
  const [selectedItems, setSelectedItems] = useState<Set<number>>(new Set());
  const [fixingItems, setFixingItems] = useState<Set<number>>(new Set());
  const [fixedItems, setFixedItems] = useState<Set<number>>(new Set());
  const [fixErrors, setFixErrors] = useState<Map<number, string>>(new Map());
  const [isFixingAll, setIsFixingAll] = useState(false);

  // Mutations
  const uploadMutation = trpc.audit.uploadSpreadsheet.useMutation();
  const parseMutation = trpc.audit.parseSheet.useMutation();
  const startAuditMutation = trpc.audit.startAudit.useMutation();
  const fixPriceMutation = trpc.audit.fixPrice.useMutation();
  const fixPricesMutation = trpc.audit.fixPrices.useMutation();

  // Polling progress
  const progressQuery = trpc.audit.getProgress.useQuery(
    { jobId },
    { enabled: !!jobId && step === "running", refetchInterval: 2000 }
  );

  // Fetch results when audit completes
  const resultsQuery = trpc.audit.getResults.useQuery(
    { jobId },
    { enabled: !!jobId && step === "results" }
  );

  // Handle progress completion
  useEffect(() => {
    if (progressQuery.data?.status === "completed") {
      setStep("results");
    }
  }, [progressQuery.data?.status]);

  // Handle results loaded
  useEffect(() => {
    if (resultsQuery.data) {
      setResults(resultsQuery.data.results);
      setSummary(resultsQuery.data.summary);
    }
  }, [resultsQuery.data]);

  // File upload handler
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.match(/\.(xlsx|xls)$/i)) {
      toast.error("Formato inválido. Envie um arquivo Excel (.xlsx ou .xls)");
      return;
    }

    const reader = new FileReader();
    reader.onload = async () => {
      const base64 = (reader.result as string).split(",")[1];
      setFileBase64(base64);
      setFileName(file.name);

      try {
        const result = await uploadMutation.mutateAsync({ fileBase64: base64, fileName: file.name });
        setSheets(result.sheets);
        if (result.sheets.length > 0) {
          setSelectedSheet(result.sheets[0]);
        }
        setStep("configure");
        toast.success(`Planilha carregada! ${result.sheets.length} aba(s) encontrada(s)`);
      } catch (error: any) {
        toast.error(`Erro ao carregar: ${error?.message}`);
      }
    };
    reader.readAsDataURL(file);
  };

  // Parse sheet handler
  const handleParseSheet = async () => {
    if (!selectedSheet || !fileBase64) return;
    try {
      const result = await parseMutation.mutateAsync({ fileBase64, sheetName: selectedSheet });
      setParseResult(result);
      setAuditId(result.auditId);
      toast.success(`Aba analisada! ${result.totalProducts} produtos, ${result.accounts.length} contas detectadas`);
    } catch (error: any) {
      toast.error(`Erro ao analisar: ${error?.message}`);
    }
  };

  // Start audit handler
  const handleStartAudit = async () => {
    if (!auditId) return;
    try {
      const result = await startAuditMutation.mutateAsync({ auditId });
      setJobId(result.jobId);
      setStep("running");
    } catch (error: any) {
      toast.error(`Erro ao iniciar: ${error?.message}`);
    }
  };

  // Reset handler
  const handleReset = () => {
    setStep("upload");
    setFileBase64("");
    setFileName("");
    setSheets([]);
    setSelectedSheet("");
    setAuditId("");
    setJobId("");
    setParseResult(null);
    setResults([]);
    setSummary(null);
    setSearchQuery("");
    setDivergenceFilter("all");
    setPlatformFilter("all");
    setAccountFilter("all");
    setCurrentPage(1);
    setSelectedItems(new Set());
    setFixingItems(new Set());
    setFixedItems(new Set());
    setFixErrors(new Map());
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // Filtered and sorted results
  const filteredResults = useMemo(() => {
    let filtered = results;

    if (divergenceFilter !== "all") {
      filtered = filtered.filter(r => r.divergenceType === divergenceFilter);
    }
    if (platformFilter !== "all") {
      filtered = filtered.filter(r => r.platform === platformFilter);
    }
    if (accountFilter !== "all") {
      filtered = filtered.filter(r => r.accountName === accountFilter);
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(r =>
        r.productName.toLowerCase().includes(q) ||
        r.accountName.toLowerCase().includes(q) ||
        (r.matchedTitle && r.matchedTitle.toLowerCase().includes(q)) ||
        (r.sku && r.sku.toLowerCase().includes(q))
      );
    }

    // Sort
    const sortOrder: Record<string, number> = { price_mismatch: 0, missing: 1, paused: 2, ok: 3 };
    filtered.sort((a, b) => {
      if (sortField === "divergenceType") {
        const diff = (sortOrder[a.divergenceType] ?? 4) - (sortOrder[b.divergenceType] ?? 4);
        return sortDir === "asc" ? diff : -diff;
      }
      if (sortField === "priceDiff") {
        const aVal = Math.abs(a.priceDiff ?? 0);
        const bVal = Math.abs(b.priceDiff ?? 0);
        return sortDir === "asc" ? aVal - bVal : bVal - aVal;
      }
      if (sortField === "productName") {
        return sortDir === "asc" ? a.productName.localeCompare(b.productName) : b.productName.localeCompare(a.productName);
      }
      return 0;
    });

    return filtered;
  }, [results, divergenceFilter, platformFilter, accountFilter, searchQuery, sortField, sortDir]);

  const totalPages = Math.ceil(filteredResults.length / PAGE_SIZE);
  const paginatedResults = filteredResults.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  // Unique accounts for filter
  const uniqueAccounts = useMemo(() => {
    const accounts = new Set<string>();
    results.forEach(r => accounts.add(r.accountName));
    return Array.from(accounts).sort();
  }, [results]);

  const uniquePlatforms = useMemo(() => {
    const platforms = new Set<string>();
    results.forEach(r => platforms.add(r.platform));
    return Array.from(platforms).sort();
  }, [results]);

  const toggleSort = (field: string) => {
    if (sortField === field) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  };

  // Get the original index of a result item in the full results array
  const getOriginalIndex = useCallback((item: AuditResultItem) => {
    return results.findIndex(r =>
      r.productName === item.productName &&
      r.accountName === item.accountName &&
      r.platform === item.platform &&
      r.listingType === item.listingType &&
      r.externalId === item.externalId
    );
  }, [results]);

  // Items that can be fixed (price_mismatch with externalId)
  const fixableFilteredItems = useMemo(() => {
    return filteredResults
      .map((item, _) => ({ item, originalIndex: getOriginalIndex(item) }))
      .filter(({ item }) => item.divergenceType === "price_mismatch" && item.externalId);
  }, [filteredResults, getOriginalIndex]);

  // Selection handlers
  const toggleSelectItem = (originalIndex: number) => {
    setSelectedItems(prev => {
      const next = new Set(prev);
      if (next.has(originalIndex)) {
        next.delete(originalIndex);
      } else {
        next.add(originalIndex);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    const fixableIndices = fixableFilteredItems.map(f => f.originalIndex);
    const allSelected = fixableIndices.every(idx => selectedItems.has(idx));
    if (allSelected) {
      setSelectedItems(prev => {
        const next = new Set(prev);
        fixableIndices.forEach(idx => next.delete(idx));
        return next;
      });
    } else {
      setSelectedItems(prev => {
        const next = new Set(prev);
        fixableIndices.forEach(idx => next.add(idx));
        return next;
      });
    }
  };

  // Fix single price
  const handleFixSingle = async (item: AuditResultItem, originalIndex: number) => {
    if (!item.externalId) return;
    setFixingItems(prev => new Set(prev).add(originalIndex));
    setFixErrors(prev => { const n = new Map(prev); n.delete(originalIndex); return n; });

    try {
      await fixPriceMutation.mutateAsync({
        accountName: item.accountName,
        platform: item.platform,
        externalId: item.externalId,
        expectedPrice: item.expectedPrice,
        sku: item.sku,
      });
      setFixedItems(prev => new Set(prev).add(originalIndex));
      toast.success(`Preço corrigido: ${item.productName} → R$${Math.round(item.expectedPrice)}`);
    } catch (error: any) {
      const msg = error?.message || "Erro";
      setFixErrors(prev => new Map(prev).set(originalIndex, msg));
      if (msg.includes("bloqueado") || msg.includes("promoção")) {
        toast.warning(`${item.productName}: Preço bloqueado (promoção ativa). Altere manualmente no marketplace.`);
      } else {
        toast.error(`Erro ao corrigir ${item.productName}: ${msg}`);
      }
    } finally {
      setFixingItems(prev => { const n = new Set(prev); n.delete(originalIndex); return n; });
    }
  };

  // Fix selected prices in batch
  const handleFixSelected = async () => {
    const itemsToFix = Array.from(selectedItems)
      .map(idx => results[idx])
      .filter(item => item && item.divergenceType === "price_mismatch" && item.externalId);

    if (itemsToFix.length === 0) {
      toast.error("Nenhum item selecionado para corrigir");
      return;
    }

    setIsFixingAll(true);
    const indices = Array.from(selectedItems);
    indices.forEach(idx => setFixingItems(prev => new Set(prev).add(idx)));

    try {
      const result = await fixPricesMutation.mutateAsync({
        items: itemsToFix.map(item => ({
          accountName: item.accountName,
          platform: item.platform,
          externalId: item.externalId!,
          expectedPrice: item.expectedPrice,
          sku: item.sku,
        })),
      });

      // Map results back to original indices
      let resultIdx = 0;
      let blockedCount = 0;
      for (const idx of indices) {
        const item = results[idx];
        if (!item || !item.externalId || item.divergenceType !== "price_mismatch") continue;
        const res = result.results[resultIdx];
        if (res) {
          if (res.success) {
            setFixedItems(prev => new Set(prev).add(idx));
          } else {
            setFixErrors(prev => new Map(prev).set(idx, res.message));
            if (res.message.includes("bloqueado") || res.message.includes("promoção") || res.message.includes("not_modifiable")) {
              blockedCount++;
            }
          }
          resultIdx++;
        }
      }

      const realErrors = result.summary.errors - blockedCount;
      let msg = `Correção concluída: ${result.summary.success} corrigidos`;
      if (blockedCount > 0) msg += `, ${blockedCount} bloqueados (promoção)`;
      if (realErrors > 0) msg += `, ${realErrors} erros`;
      toast.success(msg);
      setSelectedItems(new Set());
    } catch (error: any) {
      toast.error(`Erro na correção em massa: ${error?.message}`);
    } finally {
      setIsFixingAll(false);
      setFixingItems(new Set());
    }
  };

  // Fix ALL price_mismatch items (not just selected)
  const handleFixAllMismatches = async () => {
    const allMismatches = results
      .map((item, idx) => ({ item, idx }))
      .filter(({ item }) => item.divergenceType === "price_mismatch" && item.externalId && !fixedItems.has(0));

    // Filter out already fixed
    const itemsToFix = allMismatches.filter(({ idx }) => !fixedItems.has(idx));

    if (itemsToFix.length === 0) {
      toast.info("Todos os preços já foram corrigidos!");
      return;
    }

    setIsFixingAll(true);
    itemsToFix.forEach(({ idx }) => setFixingItems(prev => new Set(prev).add(idx)));

    try {
      const result = await fixPricesMutation.mutateAsync({
        items: itemsToFix.map(({ item }) => ({
          accountName: item.accountName,
          platform: item.platform,
          externalId: item.externalId!,
          expectedPrice: item.expectedPrice,
          sku: item.sku,
        })),
      });

      let blockedCount = 0;
      result.results.forEach((res, i) => {
        const { idx } = itemsToFix[i];
        if (res.success) {
          setFixedItems(prev => new Set(prev).add(idx));
        } else {
          setFixErrors(prev => new Map(prev).set(idx, res.message));
          if (res.message.includes("bloqueado") || res.message.includes("promoção") || res.message.includes("not_modifiable")) {
            blockedCount++;
          }
        }
      });

      const realErrors = result.summary.errors - blockedCount;
      let msg = `Correção concluída: ${result.summary.success} corrigidos`;
      if (blockedCount > 0) msg += `, ${blockedCount} bloqueados (promoção)`;
      if (realErrors > 0) msg += `, ${realErrors} erros`;
      toast.success(msg);
    } catch (error: any) {
      toast.error(`Erro na correção em massa: ${error?.message}`);
    } finally {
      setIsFixingAll(false);
      setFixingItems(new Set());
    }
  };

  // Count of fixable items
  const fixableCount = useMemo(() => {
    return results.filter(r => r.divergenceType === "price_mismatch" && r.externalId && !fixedItems.has(results.indexOf(r))).length;
  }, [results, fixedItems]);

  // Export CSV
  const handleExportCSV = () => {
    const headers = ["Produto", "Conta", "Plataforma", "Tipo", "Preço Esperado", "Preço Real", "Diferença", "Status", "Resultado", "ID Anúncio", "SKU"];
    const rows = filteredResults.map(r => [
      r.productName,
      r.accountName,
      PLATFORM_LABELS[r.platform] || r.platform,
      r.listingType,
      Math.round(r.expectedPrice).toString(),
      r.actualPrice?.toFixed(2) ?? "",
      r.priceDiff?.toFixed(2) ?? "",
      r.status ?? "",
      DIVERGENCE_CONFIG[r.divergenceType]?.label ?? r.divergenceType,
      r.externalId ?? "",
      r.sku ?? "",
    ]);

    const csv = [headers.join(";"), ...rows.map(r => r.join(";"))].join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `auditoria_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Auditoria de Anúncios</h1>
          <p className="text-muted-foreground">Compare preços da planilha com os anúncios publicados nos marketplaces</p>
        </div>
        {step !== "upload" && (
          <Button variant="outline" onClick={handleReset}>Nova Auditoria</Button>
        )}
      </div>

      {/* Step 1: Upload */}
      {step === "upload" && (
        <Card className="border-dashed border-2 border-muted-foreground/25">
          <CardContent className="flex flex-col items-center justify-center py-16 gap-4">
            <div className="rounded-full bg-primary/10 p-4">
              <FileSpreadsheet className="h-10 w-10 text-primary" />
            </div>
            <div className="text-center">
              <h3 className="text-lg font-semibold">Envie sua planilha Excel</h3>
              <p className="text-sm text-muted-foreground mt-1">
                Formato aceito: .xlsx ou .xls com preços por conta/marketplace
              </p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              onChange={handleFileSelect}
              className="hidden"
            />
            <Button
              size="lg"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadMutation.isPending}
            >
              {uploadMutation.isPending ? (
                <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Carregando...</>
              ) : (
                <><Upload className="h-4 w-4 mr-2" /> Selecionar Arquivo</>
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Step 2: Configure */}
      {step === "configure" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileSpreadsheet className="h-5 w-5" />
                {fileName}
              </CardTitle>
              <CardDescription>Selecione a aba e revise os dados detectados</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-4 items-end">
                <div className="flex-1">
                  <label className="text-sm font-medium mb-1 block">Aba da Planilha</label>
                  <Select value={selectedSheet} onValueChange={setSelectedSheet}>
                    <SelectTrigger>
                      <SelectValue placeholder="Selecione uma aba" />
                    </SelectTrigger>
                    <SelectContent>
                      {sheets.map(s => (
                        <SelectItem key={s} value={s}>{s}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Button onClick={handleParseSheet} disabled={parseMutation.isPending || !selectedSheet}>
                  {parseMutation.isPending ? (
                    <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Analisando...</>
                  ) : (
                    <><Search className="h-4 w-4 mr-2" /> Analisar Aba</>
                  )}
                </Button>
              </div>

              {parseResult && (
                <div className="space-y-4 mt-4">
                  <div className="grid grid-cols-3 gap-4">
                    <Card className="bg-muted/50">
                      <CardContent className="pt-4 text-center">
                        <div className="text-2xl font-bold">{parseResult.totalProducts}</div>
                        <div className="text-sm text-muted-foreground">Produtos</div>
                      </CardContent>
                    </Card>
                    <Card className="bg-muted/50">
                      <CardContent className="pt-4 text-center">
                        <div className="text-2xl font-bold">{parseResult.accounts.length}</div>
                        <div className="text-sm text-muted-foreground">Contas</div>
                      </CardContent>
                    </Card>
                    <Card className="bg-muted/50">
                      <CardContent className="pt-4 text-center">
                        <div className="text-2xl font-bold">{parseResult.totalColumns}</div>
                        <div className="text-sm text-muted-foreground">Colunas de Preço</div>
                      </CardContent>
                    </Card>
                  </div>

                  <div>
                    <h4 className="text-sm font-medium mb-2">Contas Detectadas:</h4>
                    <div className="flex flex-wrap gap-2">
                      {parseResult.accounts.map((acc: any, i: number) => (
                        <Badge key={i} variant="outline" className={PLATFORM_COLORS[acc.platform] || ""}>
                          {PLATFORM_LABELS[acc.platform] || acc.platform} — {acc.accountName} ({acc.columns} col.)
                        </Badge>
                      ))}
                    </div>
                  </div>

                  <Button size="lg" className="w-full" onClick={handleStartAudit} disabled={startAuditMutation.isPending}>
                    {startAuditMutation.isPending ? (
                      <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Iniciando...</>
                    ) : (
                      <><Play className="h-4 w-4 mr-2" /> Iniciar Auditoria</>
                    )}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Step 3: Running */}
      {step === "running" && (
        <Card>
          <CardContent className="py-12 space-y-6">
            <div className="text-center">
              <Loader2 className="h-12 w-12 animate-spin text-primary mx-auto mb-4" />
              <h3 className="text-lg font-semibold">Auditoria em andamento...</h3>
              <p className="text-sm text-muted-foreground mt-1">
                Buscando preços reais em cada marketplace. Isso pode levar alguns minutos.
              </p>
            </div>

            {progressQuery.data && (
              <div className="space-y-2 max-w-md mx-auto">
                <Progress value={progressQuery.data.total > 0 ? (progressQuery.data.processed / progressQuery.data.total) * 100 : 0} />
                <div className="flex justify-between text-sm text-muted-foreground">
                  <span>{progressQuery.data.processed} / {progressQuery.data.total} contas</span>
                  <span>{Math.round(progressQuery.data.total > 0 ? (progressQuery.data.processed / progressQuery.data.total) * 100 : 0)}%</span>
                </div>
                {progressQuery.data.currentStep && (
                  <p className="text-sm text-center text-muted-foreground">{progressQuery.data.currentStep}</p>
                )}
              </div>
            )}

            <p className="text-xs text-center text-muted-foreground">
              Você pode fechar este dialog — o processo continua em segundo plano.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Step 4: Results */}
      {step === "results" && summary && (
        <div className="space-y-4">
          {/* Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Card className="cursor-pointer hover:ring-2 ring-primary/50 transition-all" onClick={() => { setDivergenceFilter("all"); setCurrentPage(1); }}>
              <CardContent className="pt-4 text-center">
                <BarChart3 className="h-5 w-5 mx-auto mb-1 text-primary" />
                <div className="text-2xl font-bold">{summary.total}</div>
                <div className="text-xs text-muted-foreground">Total</div>
              </CardContent>
            </Card>
            <Card className="cursor-pointer hover:ring-2 ring-emerald-300 transition-all" onClick={() => { setDivergenceFilter("ok"); setCurrentPage(1); }}>
              <CardContent className="pt-4 text-center">
                <CheckCircle2 className="h-5 w-5 mx-auto mb-1 text-emerald-600" />
                <div className="text-2xl font-bold text-emerald-600">{summary.ok}</div>
                <div className="text-xs text-muted-foreground">OK</div>
              </CardContent>
            </Card>
            <Card className="cursor-pointer hover:ring-2 ring-amber-300 transition-all" onClick={() => { setDivergenceFilter("price_mismatch"); setCurrentPage(1); }}>
              <CardContent className="pt-4 text-center">
                <AlertTriangle className="h-5 w-5 mx-auto mb-1 text-amber-600" />
                <div className="text-2xl font-bold text-amber-600">{summary.priceMismatch}</div>
                <div className="text-xs text-muted-foreground">Preço Diferente</div>
              </CardContent>
            </Card>
            <Card className="cursor-pointer hover:ring-2 ring-red-300 transition-all" onClick={() => { setDivergenceFilter("missing"); setCurrentPage(1); }}>
              <CardContent className="pt-4 text-center">
                <XCircle className="h-5 w-5 mx-auto mb-1 text-red-600" />
                <div className="text-2xl font-bold text-red-600">{summary.missing}</div>
                <div className="text-xs text-muted-foreground">Não Encontrado</div>
              </CardContent>
            </Card>
            <Card className="cursor-pointer hover:ring-2 ring-gray-300 transition-all" onClick={() => { setDivergenceFilter("paused"); setCurrentPage(1); }}>
              <CardContent className="pt-4 text-center">
                <MinusCircle className="h-5 w-5 mx-auto mb-1 text-gray-500" />
                <div className="text-2xl font-bold text-gray-500">{summary.paused}</div>
                <div className="text-xs text-muted-foreground">Pausado</div>
              </CardContent>
            </Card>
          </div>

          {/* Fix Price Actions Bar */}
          {summary.priceMismatch > 0 && (
            <Card className="border-amber-200 bg-amber-50">
              <CardContent className="py-3 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <Wrench className="h-5 w-5 text-amber-600" />
                  <div>
                    <span className="font-medium text-amber-600">{fixableCount}</span>
                    <span className="text-sm text-muted-foreground ml-1">anúncio(s) com preço diferente para corrigir</span>
                    {fixedItems.size > 0 && (
                      <span className="text-sm text-emerald-600 ml-2">({fixedItems.size} já corrigido(s))</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {selectedItems.size > 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-amber-300 text-amber-600 hover:bg-amber-50"
                      onClick={handleFixSelected}
                      disabled={isFixingAll}
                    >
                      {isFixingAll ? (
                        <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Corrigindo...</>
                      ) : (
                        <><Wrench className="h-4 w-4 mr-2" /> Corrigir Selecionados ({selectedItems.size})</>
                      )}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    className="bg-amber-600 hover:bg-amber-700 text-white"
                    onClick={handleFixAllMismatches}
                    disabled={isFixingAll || fixableCount === 0}
                  >
                    {isFixingAll ? (
                      <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Corrigindo Todos...</>
                    ) : (
                      <><CheckCheck className="h-4 w-4 mr-2" /> Corrigir Todos ({fixableCount})</>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Filters */}
          <div className="flex flex-wrap gap-3 items-center">
            <div className="flex-1 min-w-[200px]">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Buscar produto, conta ou SKU..."
                  value={searchQuery}
                  onChange={e => { setSearchQuery(e.target.value); setCurrentPage(1); }}
                  className="pl-9"
                />
              </div>
            </div>

            <Select value={divergenceFilter} onValueChange={(v) => { setDivergenceFilter(v as DivergenceFilter); setCurrentPage(1); }}>
              <SelectTrigger className="w-[180px]">
                <Filter className="h-4 w-4 mr-2" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos os Status</SelectItem>
                <SelectItem value="price_mismatch">Preço Diferente</SelectItem>
                <SelectItem value="missing">Não Encontrado</SelectItem>
                <SelectItem value="paused">Pausado</SelectItem>
                <SelectItem value="ok">OK</SelectItem>
              </SelectContent>
            </Select>

            <Select value={platformFilter} onValueChange={(v) => { setPlatformFilter(v); setCurrentPage(1); }}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todas Plataformas</SelectItem>
                {uniquePlatforms.map(p => (
                  <SelectItem key={p} value={p}>{PLATFORM_LABELS[p] || p}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={accountFilter} onValueChange={(v) => { setAccountFilter(v); setCurrentPage(1); }}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todas Contas</SelectItem>
                {uniqueAccounts.map(a => (
                  <SelectItem key={a} value={a}>{a}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button variant="outline" size="sm" onClick={handleExportCSV}>
              <Download className="h-4 w-4 mr-2" /> Exportar CSV
            </Button>
          </div>

          {/* Results count */}
          <div className="text-sm text-muted-foreground">
            Mostrando {filteredResults.length} de {results.length} resultados
          </div>

          {/* Results Table */}
          <div className="rounded-lg border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-muted/50 border-b">
                    <th className="p-3 w-10">
                      <Checkbox
                        checked={fixableFilteredItems.length > 0 && fixableFilteredItems.every(f => selectedItems.has(f.originalIndex))}
                        onCheckedChange={toggleSelectAll}
                      />
                    </th>
                    <th className="text-left p-3 font-medium cursor-pointer hover:text-primary" onClick={() => toggleSort("productName")}>
                      <span className="flex items-center gap-1">Produto <ArrowUpDown className="h-3 w-3" /></span>
                    </th>
                    <th className="text-left p-3 font-medium">Conta</th>
                    <th className="text-left p-3 font-medium">Plataforma</th>
                    <th className="text-right p-3 font-medium">Preço Planilha</th>
                    <th className="text-right p-3 font-medium">Preço Real</th>
                    <th className="text-right p-3 font-medium cursor-pointer hover:text-primary" onClick={() => toggleSort("priceDiff")}>
                      <span className="flex items-center justify-end gap-1">Diferença <ArrowUpDown className="h-3 w-3" /></span>
                    </th>
                    <th className="text-center p-3 font-medium cursor-pointer hover:text-primary" onClick={() => toggleSort("divergenceType")}>
                      <span className="flex items-center justify-center gap-1">Status <ArrowUpDown className="h-3 w-3" /></span>
                    </th>
                    <th className="text-center p-3 font-medium w-[100px]">Ação</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedResults.map((r, i) => {
                    const config = DIVERGENCE_CONFIG[r.divergenceType];
                    const Icon = config?.icon;
                    const originalIndex = getOriginalIndex(r);
                    const isFixable = r.divergenceType === "price_mismatch" && r.externalId;
                    const isFixing = fixingItems.has(originalIndex);
                    const isFixed = fixedItems.has(originalIndex);
                    const fixError = fixErrors.get(originalIndex);

                    return (
                      <tr key={i} className={`border-b hover:bg-muted/30 transition-colors ${isFixed ? "bg-emerald-50" : ""} ${fixError ? "bg-red-50" : ""}`}>
                        <td className="p-3">
                          {isFixable && !isFixed && (
                            <Checkbox
                              checked={selectedItems.has(originalIndex)}
                              onCheckedChange={() => toggleSelectItem(originalIndex)}
                            />
                          )}
                          {isFixed && <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
                        </td>
                        <td className="p-3">
                          <div className="font-medium truncate max-w-[300px]" title={r.productName}>
                            {r.productName}
                          </div>
                          {r.sku && <div className="text-xs text-muted-foreground">{r.sku}</div>}
                        </td>
                        <td className="p-3 capitalize">{r.accountName}</td>
                        <td className="p-3">
                          <Badge variant="outline" className={`text-xs ${PLATFORM_COLORS[r.platform] || ""}`}>
                            {PLATFORM_LABELS[r.platform] || r.platform}
                          </Badge>
                          {r.listingType && r.listingType !== "shopee" && r.listingType !== "temu" && (
                            <span className="text-xs text-muted-foreground ml-1">{r.listingType}</span>
                          )}
                        </td>
                        <td className="p-3 text-right font-mono">R$ {Math.round(r.expectedPrice).toLocaleString("pt-BR")}</td>
                        <td className="p-3 text-right font-mono">
                          {r.actualPrice !== null ? `R$ ${r.actualPrice.toFixed(2)}` : "—"}
                        </td>
                        <td className="p-3 text-right font-mono">
                          {r.priceDiff !== null ? (
                            <span className={r.priceDiff > 0 ? "text-emerald-600" : r.priceDiff < 0 ? "text-red-600" : "text-muted-foreground"}>
                              {r.priceDiff > 0 ? "+" : ""}{r.priceDiff.toFixed(2)}
                            </span>
                          ) : "—"}
                        </td>
                        <td className="p-3 text-center">
                          {isFixed ? (
                            <Badge variant="outline" className="text-xs bg-emerald-50 text-emerald-600 border-emerald-200">
                              <CheckCircle2 className="h-3 w-3 mr-1" /> Corrigido
                            </Badge>
                          ) : fixError ? (
                            fixError.includes("bloqueado") || fixError.includes("promoção") || fixError.includes("not_modifiable") ? (
                              <Badge variant="outline" className="text-xs bg-purple-50 text-purple-600 border-purple-200" title={fixError}>
                                <AlertTriangle className="h-3 w-3 mr-1" /> Bloqueado
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="text-xs bg-red-50 text-red-600 border-red-200" title={fixError}>
                                <XCircle className="h-3 w-3 mr-1" /> Erro
                              </Badge>
                            )
                          ) : (
                            <Badge variant="outline" className={`text-xs ${config?.color || ""}`}>
                              {Icon && <Icon className="h-3 w-3 mr-1" />}
                              {config?.label || r.divergenceType}
                            </Badge>
                          )}
                        </td>
                        <td className="p-3 text-center">
                          {isFixable && !isFixed && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-amber-600 hover:text-amber-700 hover:bg-amber-50"
                              onClick={() => handleFixSingle(r, originalIndex)}
                              disabled={isFixing || isFixingAll}
                              title={`Corrigir para R$${Math.round(r.expectedPrice)}`}
                            >
                              {isFixing ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <><Wrench className="h-3.5 w-3.5 mr-1" /> Corrigir</>
                              )}
                            </Button>
                          )}
                          {isFixed && (
                            <span className="text-xs text-emerald-600">R${Math.round(r.expectedPrice)}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {paginatedResults.length === 0 && (
                    <tr>
                      <td colSpan={9} className="p-8 text-center text-muted-foreground">
                        Nenhum resultado encontrado com os filtros aplicados.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between">
              <div className="text-sm text-muted-foreground">
                Página {currentPage} de {totalPages}
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={currentPage <= 1} onClick={() => setCurrentPage(p => p - 1)}>
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="sm" disabled={currentPage >= totalPages} onClick={() => setCurrentPage(p => p + 1)}>
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
