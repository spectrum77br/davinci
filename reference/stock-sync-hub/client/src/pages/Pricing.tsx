import DashboardLayout from "@/components/DashboardLayout";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { trpc } from "@/lib/trpc";
import { toast } from "sonner";
import { useState, useMemo, useRef, useEffect, useCallback, Fragment } from "react";
import { Plus, Trash2, Settings2, Upload, Search, DollarSign, Save, X, Check, Download, Undo2, Redo2, Send, Loader2, AlertCircle, CheckCircle2, Smartphone, Briefcase, ChevronDown, Store, Eye, EyeOff, Copy, ExternalLink, RefreshCw, Zap, AlertTriangle, TrendingDown, TrendingUp, BarChart3, Link2 } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger, DropdownMenuLabel } from "@/components/ui/dropdown-menu";

// ── Types ──

type Department = "celular" | "mala" | "eletro" | "catalogo";
type ViewMode = "pricing" | "loja";

type StoreInfoItem = {
  id: number;
  platform: string;
  segment: string | null;
  freight: string | null;
  cpfName: string | null;
  accountName: string | null;
  server: string | null;
  cnpj: string | null;
  email: string | null;
  observation: string | null;
  shippingAddress: string | null;
  returnAddress: string | null;
  phone: string | null;
  password: string | null;
  link: string | null;
  sortOrder: number;
};

type PricingAccount = {
  id: number;
  name: string;
  platform: string;
  listingType: string | null;
  department: Department | string;
  kitNumber: number;
  commission: string;
  transport: string | null;
  margin1: string | null;
  shipping1: string | null;
  margin2: string | null;
  shipping2: string | null;
  margin3: string | null;
  shipping3: string | null;
  margin4: string | null;
  shipping4: string | null;
  margin5: string | null;
  shipping5: string | null;
  observation: string | null;
  observation2: string | null;
  observation3: string | null;
  storeInfoId: number | null;
  integrationId: number | null;
  sortOrder: number | null;
  isActive: boolean;
};

type PricingProduct = {
  id: number;
  sku: string;
  name: string;
  department: Department | string;
  productType: number; // 1-5
  blingCostPrice: string | null;
  costKit1: string;
  costKit2: string | null;
  costKit3: string | null;
  costKit4: string | null;
  description: string | null;
  model: string | null;
  ean: string | null;
  isActive: boolean;
};

type PricingOverride = {
  id: number;
  pricingProductId: number;
  pricingAccountId: number;
  priceOverride: string | null;
  cellStatus: string | null;
};

// ── Type labels ──

const CELULAR_TYPE_LABELS: Record<number, string> = {
  1: "Acessórios",
  2: "Diversos",
  3: "Regular",
  4: "Robusto",
  5: "Apple",
};

const MALA_TYPE_LABELS: Record<number, string> = {
  1: 'Acessórios',
  2: '12"',
  3: '18" e 20"',
  4: '24" acima',
  5: "Queima Estoque",
};

const TYPE_COLORS: Record<number, string> = {
  1: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  2: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  3: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400",
  4: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
  5: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
};

function getTypeLabel(dept: Department, type: number): string {
  return (dept === "celular" || dept === "catalogo" || dept === "eletro") ? (CELULAR_TYPE_LABELS[type] || `Tipo ${type}`) : (MALA_TYPE_LABELS[type] || `Tipo ${type}`);
}

// ── Helpers ──

/**
 * Fórmula da planilha sku3.xlsx:
 * Preço = ((CUSTO × MARGEM) + FRETE + CUSTO) / (1 - COMISSÃO)
 */
function calcPrice(cost: number, margin: number, shipping: number, commission: number): number {
  if (commission >= 1) return 0;
  return Math.round(((cost * margin) + shipping + cost) / (1 - commission));
}

/**
 * Retorna o custo do produto baseado no kit da conta
 */
function getKitCost(product: PricingProduct, kitNumber: number): number {
  switch (kitNumber) {
    case 1: return parseFloat(product.costKit1) || 0;
    case 2: return parseFloat(product.costKit2 || product.costKit1) || 0;
    case 3: return parseFloat(product.costKit3 || product.costKit1) || 0;
    case 4: return parseFloat(product.costKit4 || product.costKit1) || 0;
    default: return parseFloat(product.costKit1) || 0;
  }
}

/**
 * Retorna margem e frete da conta para o tipo do produto
 */
function getMarginShipping(account: PricingAccount, productType: number): { margin: number; shipping: number } | null {
  const marginKey = `margin${productType}` as keyof PricingAccount;
  const shippingKey = `shipping${productType}` as keyof PricingAccount;
  const marginVal = account[marginKey] as string | null;
  const shippingVal = account[shippingKey] as string | null;
  
  if (!marginVal || marginVal === "-" || marginVal === "") return null;
  
  const margin = parseFloat(marginVal);
  const shipping = parseFloat(shippingVal || "0");
  
  if (isNaN(margin)) return null;
  return { margin, shipping: isNaN(shipping) ? 0 : shipping };
}

// ── Group accounts ──

interface AccountGroup {
  label: string;
  accounts: PricingAccount[];
}

function groupAccountsByPlatform(accounts: PricingAccount[]): AccountGroup[] {
  const sorted = [...accounts].sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0));
  const groups: AccountGroup[] = [];
  const platformOrder = ["amazon", "magalu", "mercadolivre", "shopee", "temu", "aliexpress", "tiktok"];
  
  for (const platform of platformOrder) {
    const accs = sorted.filter(a => a.platform === platform);
    if (accs.length === 0) continue;
    
    const platformLabel = platform === "mercadolivre" ? "ML" :
      platform === "shopee" ? "Shopee" :
      platform === "amazon" ? "Amazon" :
      platform === "magalu" ? "Magalu" :
      platform === "temu" ? "Temu" :
      platform === "aliexpress" ? "AliExpress" :
      platform === "tiktok" ? "TikTok" : platform;
    
    // Agrupar por kit number
    const byKit = new Map<number, PricingAccount[]>();
    for (const acc of accs) {
      if (!byKit.has(acc.kitNumber)) byKit.set(acc.kitNumber, []);
      byKit.get(acc.kitNumber)!.push(acc);
    }
    for (const [kit, kitAccs] of Array.from(byKit).sort((a, b) => a[0] - b[0])) {
      // Ordenar contas: agrupar por nome base (sem classico/premium) e dentro classico antes de premium
      kitAccs.sort((a, b) => {
        const getBaseName = (name: string) => name.replace(/\s*(classico|premium|cl[aá]ssico)\s*$/i, '').trim();
        const getVariant = (name: string) => {
          if (/classico|cl[aá]ssico/i.test(name)) return 0;
          if (/premium/i.test(name)) return 1;
          return 2;
        };
        const baseA = getBaseName(a.name);
        const baseB = getBaseName(b.name);
        const cmp = baseA.localeCompare(baseB, 'pt-BR');
        if (cmp !== 0) return cmp;
        return getVariant(a.name) - getVariant(b.name);
      });
      groups.push({ label: `${platformLabel} kit ${kit}`, accounts: kitAccs });
    }
  }
  
  return groups;
}

// ── Cell coordinate system ──
type CellCoord = { row: number; col: number };
function cellKey(r: number, c: number) { return `${r}-${c}`; }

// ── Undo/Redo stack ──
type UndoEntry = { row: number; col: number; oldValue: string; newValue: string; type: string; entityId: number; field: string };

// ── Main Component ──

export default function Pricing() {
  return (
    <DashboardLayout>
      <PricingContent />
    </DashboardLayout>
  );
}

// ── Audit Row Component ──────────────────────────────────────────────────────
function AuditRow({ item, isDismissed }: { item: any; isDismissed: boolean }) {
  const utils = trpc.useUtils();
  const dismissMut = trpc.pricing.dismissAuditSku.useMutation({
    onSuccess: () => utils.pricing.getSkuAudit.invalidate(),
  });
  const undismissMut = trpc.pricing.undismissAuditSku.useMutation({
    onSuccess: () => utils.pricing.getSkuAudit.invalidate(),
  });
  return (
    <tr className={`border-t border-amber-200 dark:border-amber-700/50 hover:bg-amber-100/50 dark:hover:bg-amber-800/20 ${isDismissed ? "opacity-50" : ""}`}>
      <td className="px-2 py-1 font-mono">{item.sku}</td>
      <td className="px-2 py-1 truncate max-w-xs" title={item.title}>{item.title}</td>
      <td className="px-2 py-1 text-center font-mono text-[11px]">
        <span className={item.stock === 0 ? "text-red-600 font-bold" : "text-gray-700 dark:text-gray-300"}>{item.stock ?? "—"}</span>
      </td>
      <td className="px-2 py-1">
        {item.accounts && item.accounts.length > 0 ? (
          <span className="text-[10px]" title={item.accounts.join(", ")}>
            <span className="font-semibold text-amber-800 dark:text-amber-300">{item.accountCount}</span>
            <span className="text-gray-500 ml-0.5">({item.accounts.slice(0, 3).join(", ")}{item.accounts.length > 3 ? ` +${item.accounts.length - 3}` : ""})</span>
          </span>
        ) : (
          <span className="text-[10px] text-red-500 font-semibold">Nenhuma</span>
        )}
      </td>
      <td className="px-2 py-1">
        <div className="flex flex-wrap gap-1">
          {item.issues && item.issues.map((issue: string, idx: number) => (
            <span key={idx} className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold ${
              issue === "Sem an\u00fancio" ? "bg-red-100 text-red-700" : "bg-orange-100 text-orange-700"
            }`}>
              {issue}
            </span>
          ))}
        </div>
      </td>
      <td className="px-2 py-1 text-center">
        {isDismissed ? (
          <button
            onClick={() => undismissMut.mutate({ sku: item.sku })}
            disabled={undismissMut.isPending}
            className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700 hover:bg-green-200 disabled:opacity-50"
            title="Restaurar na auditoria"
          >
            Restaurar
          </button>
        ) : (
          <button
            onClick={() => dismissMut.mutate({ sku: item.sku })}
            disabled={dismissMut.isPending}
            className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-700 hover:bg-red-200 disabled:opacity-50"
            title="Dispensar da auditoria"
          >
            Dispensar
          </button>
        )}
      </td>
    </tr>
  );
}

function PricingContent() {
  const [department, setDepartment] = useState<Department>("celular");
  const [viewMode, setViewMode] = useState<ViewMode>("pricing");
  const [activeTab, setActiveTab] = useState("table");
  const [search, setSearch] = useState("");

  const accountsQuery = trpc.pricing.getAccounts.useQuery();
  const productsQuery = trpc.pricing.getProducts.useQuery();
  const overridesQuery = trpc.pricing.getOverrides.useQuery();

  const allAccounts = (accountsQuery.data || []) as PricingAccount[];
  const allProducts = (productsQuery.data || []) as PricingProduct[];
  const overrides = (overridesQuery.data || []) as PricingOverride[];

  const accounts = useMemo(() => allAccounts.filter(a => a.department === department), [allAccounts, department]);
  const products = useMemo(() => allProducts.filter(p => p.department === department), [allProducts, department]);

  const celularCount = useMemo(() => allAccounts.filter(a => a.department === "celular").length, [allAccounts]);
  const malaCount = useMemo(() => allAccounts.filter(a => a.department === "mala").length, [allAccounts]);
  const eletroCount = useMemo(() => allAccounts.filter(a => a.department === "eletro").length, [allAccounts]);
  const catalogoCount = useMemo(() => allAccounts.filter(a => a.department === "catalogo").length, [allAccounts]);

  const storeInfoQuery = trpc.storeInfo.list.useQuery();
  const storeInfoData = (storeInfoQuery.data || []) as StoreInfoItem[];

  const integrationsQuery = trpc.integrations.list.useQuery();
  const integrationsData = integrationsQuery.data || [];

  const isLoading = accountsQuery.isLoading || productsQuery.isLoading || overridesQuery.isLoading;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Tabela de Preços</h1>
        <p className="text-muted-foreground text-sm">Gerencie custos, margens e preços de venda por conta</p>
      </div>

      {/* Department selector */}
      <div className="flex gap-2">
        <Button
          variant={department === "celular" && viewMode === "pricing" ? "default" : "outline"}
          size="sm"
          onClick={() => { setDepartment("celular"); setViewMode("pricing"); }}
          className="gap-1.5"
        >
          <Smartphone className="h-4 w-4" />
          Celular ({celularCount} contas)
        </Button>
        <Button
          variant={department === "mala" && viewMode === "pricing" ? "default" : "outline"}
          size="sm"
          onClick={() => { setDepartment("mala"); setViewMode("pricing"); }}
          className="gap-1.5"
        >
          <Briefcase className="h-4 w-4" />
          Mala ({malaCount} contas)
        </Button>
        <Button
          variant={department === "eletro" && viewMode === "pricing" ? "default" : "outline"}
          size="sm"
          onClick={() => { setDepartment("eletro"); setViewMode("pricing"); }}
          className="gap-1.5"
        >
          <Zap className="h-4 w-4" />
          Eletro ({eletroCount} contas)
        </Button>
        <Button
          variant={department === "catalogo" && viewMode === "pricing" ? "default" : "outline"}
          size="sm"
          onClick={() => { setDepartment("catalogo"); setViewMode("pricing"); }}
          className="gap-1.5"
        >
          <BarChart3 className="h-4 w-4" />
          Catálogo ML ({catalogoCount} contas)
        </Button>
        <Button
          variant={viewMode === "loja" ? "default" : "outline"}
          size="sm"
          onClick={() => setViewMode(viewMode === "loja" ? "pricing" : "loja")}
          className="gap-1.5"
        >
          <Store className="h-4 w-4" />
          Loja ({storeInfoData.length})
        </Button>
      </div>

      {viewMode === "loja" ? (
        <StoreInfoTable stores={storeInfoData} isLoading={storeInfoQuery.isLoading} pricingAccounts={allAccounts} integrations={integrationsData} />
      ) : (
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="table">
            <DollarSign className="h-4 w-4 mr-1" />
            Tabela de Preços
          </TabsTrigger>
          <TabsTrigger value="accounts">
            <Settings2 className="h-4 w-4 mr-1" />
            Contas ({accounts.length})
          </TabsTrigger>
          <TabsTrigger value="products">
            <Upload className="h-4 w-4 mr-1" />
            Produtos ({products.length})
          </TabsTrigger>

        </TabsList>

        <TabsContent value="table" className="mt-4">
          <SpreadsheetTable
            department={department}
            accounts={accounts}
            products={products}
            overrides={overrides}
            search={search}
            setSearch={setSearch}
            isLoading={isLoading}
          />
        </TabsContent>

        <TabsContent value="accounts" className="mt-4">
          <AccountsTable department={department} accounts={accounts} />
        </TabsContent>

        <TabsContent value="products" className="mt-4">
          <ProductsTable department={department} products={products} />
        </TabsContent>


      </Tabs>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// ── SPREADSHEET TABLE (main pricing grid) ──
// ══════════════════════════════════════════════════════════════════

function SpreadsheetTable({
  department,
  accounts,
  products,
  overrides,
  search,
  setSearch,
  isLoading,
}: {
  department: Department;
  accounts: PricingAccount[];
  products: PricingProduct[];
  overrides: PricingOverride[];
  search: string;
  setSearch: (s: string) => void;
  isLoading: boolean;
}) {
  const utils = trpc.useUtils();

  // ── Mutations ──
  const updateProductMut = trpc.pricing.updateProduct.useMutation({
    onSuccess: () => utils.pricing.getProducts.invalidate(),
    onError: (err) => toast.error(`Erro ao salvar: ${err.message}`),
  });
  const setOverrideMut = trpc.pricing.setOverride.useMutation({
    onSuccess: () => utils.pricing.getOverrides.invalidate(),
    onError: (err) => toast.error(`Erro ao salvar override: ${err.message}`),
  });
  const removeOverrideMut = trpc.pricing.removeOverride.useMutation({
    onSuccess: () => utils.pricing.getOverrides.invalidate(),
  });
  const setCellStatusMut = trpc.pricing.setCellStatus.useMutation({
    onSuccess: () => utils.pricing.getOverrides.invalidate(),
  });
  const pushPriceMut = trpc.pricing.pushPrice.useMutation();
  const sendPushReportMut = trpc.pricing.sendPushReport.useMutation();
  const fetchActualPricesMut = trpc.pricing.fetchActualPrices.useMutation();
  const syncBlingCostsMut = trpc.pricing.syncBlingCosts.useMutation({
    onSuccess: (data) => {
      utils.pricing.getProducts.invalidate();
      toast.success(`Custos atualizados: ${data.updated}/${data.total} produtos`);
    },
    onError: (err) => toast.error(`Erro ao sincronizar custos: ${err.message}`),
  });
  const updateAccountMut = trpc.pricing.updateAccount.useMutation({
    onSuccess: () => utils.pricing.getAccounts.invalidate(),
    onError: (err) => toast.error(`Erro ao salvar observação: ${err.message}`),
  });

  // ── Observation editing state ──
  const [editingObsId, setEditingObsId] = useState<string | null>(null); // "accId-obsN"
  const [obsValue, setObsValue] = useState("");
  const obsInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingObsId !== null && obsInputRef.current) {
      obsInputRef.current.focus();
    }
  }, [editingObsId]);

  const commitObs = (accId: number, field: "observation" | "observation2" | "observation3") => {
    const val = obsValue.trim();
    updateAccountMut.mutate({ id: accId, [field]: val || null } as any);
    setEditingObsId(null);
    setObsValue("");
  };

  // ── State ──
  const [selectedCell, setSelectedCell] = useState<CellCoord | null>(null);
  const [editingCell, setEditingCell] = useState<CellCoord | null>(null);
  const [editValue, setEditValue] = useState("");
  const [savedCells, setSavedCells] = useState<Set<string>>(new Set());
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [redoStack, setRedoStack] = useState<UndoEntry[]>([]);
  const [pushStates, setPushStates] = useState<Map<string, string>>(new Map());
  const [clearStatusCell, setClearStatusCell] = useState<{ productId: number; accountId: number } | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const tableRef = useRef<HTMLDivElement>(null);

  const activeAccounts = useMemo(() => accounts.filter(a => a.isActive).sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0)), [accounts]);
  const accountGroups = useMemo(() => groupAccountsByPlatform(activeAccounts), [activeAccounts]);
  const allAccountsFlat = useMemo(() => accountGroups.flatMap(g => g.accounts), [accountGroups]);

  // Índices (no array flat) que iniciam um novo grupo de kit — para borda divisória grossa
  const groupStartIndices = useMemo(() => {
    const starts = new Set<number>();
    let offset = 0;
    for (const group of accountGroups) {
      starts.add(offset);
      offset += group.accounts.length;
    }
    return starts;
  }, [accountGroups]);

  // ── Compare mode state (must be after activeAccounts) ──
  const [compareProductIds, setCompareProductIds] = useState<Set<number>>(new Set()); // IDs dos produtos em modo comparação
  const [actualPriceMap, setActualPriceMap] = useState<Record<number, Record<string, { price: number | null; title: string; externalId: string; status: string }>>>({}); // integrationId → skuBase → data
  const [compareFetchingId, setCompareFetchingId] = useState<number | null>(null); // ID do produto sendo buscado

  const compareMode = compareProductIds.size > 0;

  // ── Competitor price popover state ──
  const [competitorPopoverId, setCompetitorPopoverId] = useState<number | null>(null); // product.id que está com popover aberto
  const [competitorData, setCompetitorData] = useState<Record<number, {
    grouped: Record<string, {
      minPrice: number; maxPrice: number; avgPrice: number; medianPrice: number; count: number;
      topResults: Array<{ title: string; price: number; permalink: string; sellerId: string }>;
    }>;
    totalResults: number;
    query: string;
  } | null>>({}); // productId → data
  const [competitorLoading, setCompetitorLoading] = useState<number | null>(null);
  const searchCompetitorMut = trpc.pricing.searchCompetitorPrices.useMutation();

  const handleCompetitorSearch = useCallback(async (product: PricingProduct) => {
    // Se já tem dados em cache, só abre o popover
    if (competitorData[product.id]) {
      setCompetitorPopoverId(prev => prev === product.id ? null : product.id);
      return;
    }
    // Buscar dados
    setCompetitorLoading(product.id);
    setCompetitorPopoverId(product.id);
    try {
      const res = await searchCompetitorMut.mutateAsync({
        productName: product.name,
        productSku: product.sku,
      });
      setCompetitorData(prev => ({ ...prev, [product.id]: res }));
    } catch (err: any) {
      console.error("Erro ao buscar concorrentes:", err);
      toast.error(`Erro ao buscar concorrentes: ${err.message}`);
      setCompetitorPopoverId(null);
    } finally {
      setCompetitorLoading(null);
    }
  }, [competitorData, searchCompetitorMut]);

  // Helper: calcular preço médio do produto para um listing type específico
  const getOwnPriceForListingType = useCallback((product: PricingProduct, listingType: string): number | null => {
    const matchingAccounts = activeAccounts.filter(acc => {
      if (acc.platform !== "mercadolivre") return false;
      const accLT = (acc.listingType || "").toLowerCase();
      if (listingType === "ml classico" && accLT.includes("classico")) return true;
      if (listingType === "ml premium" && accLT.includes("premium")) return true;
      return false;
    });
    if (matchingAccounts.length === 0) return null;
    // Pegar o primeiro preço válido
    for (const acc of matchingAccounts) {
      const ms = getMarginShipping(acc, product.productType);
      if (!ms) continue;
      const cost = getKitCost(product, acc.kitNumber);
      if (cost <= 0) continue;
      const commission = parseFloat(acc.commission) || 0;
      const price = calcPrice(cost, ms.margin, ms.shipping, commission);
      if (price > 0) return price;
    }
    return null;
  }, [activeAccounts]);

  const handleFetchActualPricesForProduct = useCallback(async (product: PricingProduct) => {
    // Se já está comparando esse produto, desativa
    if (compareProductIds.has(product.id)) {
      setCompareProductIds(prev => {
        const next = new Set(prev);
        next.delete(product.id);
        return next;
      });
      return;
    }
    setCompareFetchingId(product.id);
    const integrationIds = new Set<number>();
    for (const acc of activeAccounts) {
      if (acc.integrationId) integrationIds.add(acc.integrationId);
    }
    if (integrationIds.size === 0) {
      toast.warning("Nenhuma conta com integração vinculada");
      setCompareFetchingId(null);
      return;
    }
    // Buscar preços de integrações que ainda não foram buscadas
    const newMap = { ...actualPriceMap };
    for (const intId of Array.from(integrationIds)) {
      if (newMap[intId]) continue; // Já buscou essa integração
      try {
        const res = await fetchActualPricesMut.mutateAsync({ integrationId: intId });
        newMap[intId] = res.priceMap;
      } catch (err: any) {
        console.error(`Erro ao buscar preços da integração ${intId}:`, err);
        toast.error(`Erro em integração ${intId}: ${err.message}`);
      }
    }
    setActualPriceMap(newMap);
    setCompareProductIds(prev => new Set(prev).add(product.id));
    setCompareFetchingId(null);
    toast.success(`Preços de "${product.name}" carregados!`);
  }, [activeAccounts, fetchActualPricesMut, compareProductIds, actualPriceMap]);

  // Helper: get actual price for a product+account
  const getActualPrice = useCallback((product: PricingProduct, acc: PricingAccount): number | null => {
    if (!compareProductIds.has(product.id) || !acc.integrationId) return null;
    const intMap = actualPriceMap[acc.integrationId];
    if (!intMap) return null;
    // Match by SKU base
    const skuBase = product.sku.split(".")[0].split("+")[0].toLowerCase();
    const match = intMap[skuBase];
    return match?.price ?? null;
  }, [compareProductIds, actualPriceMap]);

  const overrideMap = useMemo(() => {
    const map = new Map<string, PricingOverride>();
    for (const o of overrides) {
      map.set(`${o.pricingProductId}-${o.pricingAccountId}`, o);
    }
    return map;
  }, [overrides]);

  const filteredProducts = useMemo(() => {
    if (!search) return products.filter(p => p.isActive);
    const s = search.toLowerCase();
    return products.filter(p => p.isActive && (p.sku.toLowerCase().includes(s) || p.name.toLowerCase().includes(s)));
  }, [products, search]);

  // Colunas fixas (sem SKU e Tipo): celular = Nome, Bling, Kit1, Kit2, Kit3, Kit4 (6)
  //                                  mala/eletro/catalogo = Nome, Bling, Custo (3)
  const FIXED_COLS = department === "celular" ? 6 : 3;
  const totalCols = FIXED_COLS + allAccountsFlat.length;
  const totalRows = filteredProducts.length;

  const isEditableCol = (col: number) => {
    if (col === 0) return true; // nome sempre editável
    if (col === 1) return false; // Bling cost is read-only
    if (department === "celular") return col >= 2 && col <= 5; // custos editáveis (kit1-4)
    return col === 2; // custo editável
  };
  const isPriceCol = (col: number) => col >= FIXED_COLS;

  useEffect(() => {
    if (editingCell && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editingCell]);

  const flashSaved = useCallback((row: number, col: number) => {
    const key = cellKey(row, col);
    setSavedCells(prev => new Set(prev).add(key));
    setTimeout(() => {
      setSavedCells(prev => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }, 1500);
  }, []);

  // ── Save handlers ──
  const saveCell = useCallback((row: number, col: number, value: string, addToUndo = true) => {
    const product = filteredProducts[row];
    if (!product) return;

    if (addToUndo) {
      const oldValue = getCellValue(row, col);
      setUndoStack(prev => [...prev.slice(-50), { row, col, oldValue, newValue: value, type: "product", entityId: product.id, field: col.toString() }]);
      setRedoStack([]);
    }

    // Nome é texto
    if (col === 0) { updateProductMut.mutate({ id: product.id, name: value.trim() }); flashSaved(row, col); return; }

    const val = parseFloat(value);
    if (isNaN(val) || val < 0) return;

    if (department === "celular") {
      if (col === 2) { updateProductMut.mutate({ id: product.id, costKit1: val.toFixed(2) }); flashSaved(row, col); }
      else if (col === 3) { updateProductMut.mutate({ id: product.id, costKit2: val.toFixed(2) }); flashSaved(row, col); }
      else if (col === 4) { updateProductMut.mutate({ id: product.id, costKit3: val.toFixed(2) }); flashSaved(row, col); }
      else if (col === 5) { updateProductMut.mutate({ id: product.id, costKit4: val.toFixed(2) }); flashSaved(row, col); }
    } else {
      if (col === 2) { updateProductMut.mutate({ id: product.id, costKit1: val.toFixed(2) }); flashSaved(row, col); }
    }

    if (isPriceCol(col)) {
      const accIdx = col - FIXED_COLS;
      const acc = allAccountsFlat[accIdx];
      if (!acc) return;
      setOverrideMut.mutate({
        pricingProductId: product.id,
        pricingAccountId: acc.id,
        priceOverride: val.toFixed(2),
      });
      flashSaved(row, col);
      // Preço salvo como override — push manual via botão de enviar
    }
  }, [filteredProducts, allAccountsFlat, department, updateProductMut, setOverrideMut, flashSaved, FIXED_COLS]);

  // ── Push de preço individual para marketplace ──
  const pushSinglePrice = useCallback((row: number, col: number) => {
    const product = filteredProducts[row];
    const accIdx = col - FIXED_COLS;
    const acc = allAccountsFlat[accIdx];
    if (!product || !acc || !acc.integrationId) return;

    // Calcular o preço atual (override ou calculado)
    const override = overrideMap.get(`${product.id}-${acc.id}`);
    let price: number;
    if (override?.priceOverride) {
      price = parseFloat(override.priceOverride);
    } else {
      const cost = getKitCost(product, acc.kitNumber);
      const ms = getMarginShipping(acc, product.productType);
      if (!ms) return;
      const commission = parseFloat(acc.commission) || 0;
      price = calcPrice(cost, ms.margin, ms.shipping, commission);
    }
    if (isNaN(price) || price <= 0) return;

    const ck = cellKey(row, col);
    setPushStates(prev => new Map(prev).set(ck, "pushing"));

    // Safety timeout: se o push não completar em 30s, limpar o spinner
    let completed = false;
    const safetyTimeout = setTimeout(() => {
      if (!completed) {
        setPushStates(prev => {
          if (prev.get(ck) === "pushing") {
            const n = new Map(prev);
            n.set(ck, "error");
            return n;
          }
          return prev;
        });
        toast.error(`Timeout: push de ${product.sku} → ${acc.name} demorou demais. Tente novamente.`, { duration: 8000 });
        setTimeout(() => setPushStates(prev => { const n = new Map(prev); n.delete(ck); return n; }), 5000);
      }
    }, 30000);

    pushPriceMut.mutate(
      { pricingProductId: product.id, pricingAccountId: acc.id, price },
      {
        onSuccess: (res) => {
          completed = true;
          clearTimeout(safetyTimeout);
          if (res.success) {
            setPushStates(prev => new Map(prev).set(ck, "success"));
            toast.success(`Preço R$${Math.round(price)} enviado ao ${acc.name} (${res.message})`);
            // Sempre limpar status persistido (NA/SV/error/no_link) quando push dá certo
            setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: null });
          } else if (res.results.length === 0) {
            setPushStates(prev => new Map(prev).set(ck, "no_link"));
            toast.warning(`Nenhum anúncio vinculado para ${product.sku} em ${acc.name}`);
            setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "no_link" });
          } else {
            const realErrors = res.results.filter((r: any) => !r.success && !r.skipped);
            const skipped = res.results.filter((r: any) => r.skipped);
            if (realErrors.length === 0 && skipped.length > 0) {
              setPushStates(prev => new Map(prev).set(ck, "success"));
              toast.info(`${product.sku} → ${acc.name}: ${res.message}`);
              setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "error" });
            } else {
              setPushStates(prev => new Map(prev).set(ck, "error"));
              const errMsg = realErrors.map((r: any) => r.message).join('; ');
              toast.error(`Erro ao enviar preço de ${product.sku} para ${acc.name}: ${errMsg}`, { duration: 10000 });
              setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "error" });
            }
          }
          // Sucesso limpa após 4s, erro/no_link persiste
          setTimeout(() => {
            setPushStates(prev => {
              const st = prev.get(ck);
              if (st === "success") { const n = new Map(prev); n.delete(ck); return n; }
              return prev;
            });
          }, 4000);
        },
        onError: (err) => {
          completed = true;
          clearTimeout(safetyTimeout);
          setPushStates(prev => new Map(prev).set(ck, "error"));
          toast.error(`Erro ao enviar preço: ${err.message}`);
          setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "error" });
        },
      },
    );
  }, [filteredProducts, allAccountsFlat, overrideMap, FIXED_COLS, pushPriceMut, setCellStatusMut]);

  // ── Push em massa — enviar preços para todas ou uma conta específica ──
  const [isBulkPushing, setIsBulkPushing] = useState(false);
  const [bulkPushLabel, setBulkPushLabel] = useState("");
  const pushPricesForAccounts = useCallback(async (targetAccountIds?: number[]) => {
    setIsBulkPushing(true);
    let sent = 0;
    let errors = 0;
    let noLinks = 0;
    let skipped = 0;
    const errorDetails: string[] = [];
    for (let rowIdx = 0; rowIdx < filteredProducts.length; rowIdx++) {
      const product = filteredProducts[rowIdx];
      setBulkPushLabel(prev => typeof prev === 'string' && prev ? `${prev} (${rowIdx + 1}/${filteredProducts.length})` : `${rowIdx + 1}/${filteredProducts.length}`);
      for (let accIdx = 0; accIdx < allAccountsFlat.length; accIdx++) {
        const acc = allAccountsFlat[accIdx];
        if (!acc.integrationId) continue;
        if (targetAccountIds && !targetAccountIds.includes(acc.id)) continue;
        const ms = getMarginShipping(acc, product.productType);
        const override = overrideMap.get(`${product.id}-${acc.id}`);
        if (!ms && !override?.priceOverride) continue;

        let price: number;
        if (override?.priceOverride) {
          price = parseFloat(override.priceOverride);
        } else {
          const cost = getKitCost(product, acc.kitNumber);
          const commission = parseFloat(acc.commission) || 0;
          price = calcPrice(cost, ms!.margin, ms!.shipping, commission);
        }
        if (isNaN(price) || price <= 0) continue;

        const col = FIXED_COLS + accIdx;
        const ck = cellKey(rowIdx, col);
        setPushStates(prev => new Map(prev).set(ck, "pushing"));
        try {
          const res = await pushPriceMut.mutateAsync({ pricingProductId: product.id, pricingAccountId: acc.id, price });
          if (res.success) {
            setPushStates(prev => new Map(prev).set(ck, "success"));
            sent++;
            // Sempre limpar status persistido (NA/SV/error/no_link) quando push dá certo
            setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: null });
          } else if (res.results.length === 0) {
            setPushStates(prev => new Map(prev).set(ck, "no_link"));
            noLinks++;
            // Persistir status "no_link" no banco
            setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "no_link" });
          } else {
            const realErrors = res.results.filter((r: any) => !r.success && !r.skipped);
            const skippedResults = res.results.filter((r: any) => r.skipped);
            if (realErrors.length === 0 && skippedResults.length > 0) {
              setPushStates(prev => new Map(prev).set(ck, "success"));
              skipped += skippedResults.length;
              // Persistir status "error" para anúncios encerrados
              setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "error" });
            } else {
              setPushStates(prev => new Map(prev).set(ck, "error"));
              errors++;
              const errMsg = realErrors.map((r: any) => r.message).join('; ');
              errorDetails.push(`${product.sku} → ${acc.name}: ${errMsg}`);
              // Persistir status "error" no banco
              setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "error" });
            }
          }
        } catch (e: any) {
          setPushStates(prev => new Map(prev).set(ck, "error"));
          errors++;
          errorDetails.push(`${product.sku} → ${acc.name}: ${e?.message || 'Erro desconhecido'}`);
          // Persistir status "error" no banco
          setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "error" });
        }
      }
    }
    setIsBulkPushing(false);
    setBulkPushLabel("");
    const skippedMsg = skipped > 0 ? `, ${skipped} pulado(s) (encerrados)` : '';
    if (errors > 0) {
      toast.error(`Envio: ${sent} enviado(s), ${errors} erro(s)${skippedMsg}${noLinks > 0 ? `, ${noLinks} sem vínculo` : ''}`, {
        description: errorDetails.slice(0, 5).join('\n') + (errorDetails.length > 5 ? `\n...e mais ${errorDetails.length - 5} erros` : ''),
        duration: 15000,
      });
    } else if (sent === 0 && noLinks > 0) {
      toast.warning(`Nenhum preço enviado. ${noLinks} produto(s) sem anúncio vinculado.${skippedMsg}`);
    } else {
      toast.success(`Envio: ${sent} preço(s) enviado(s) com sucesso!${skippedMsg}${noLinks > 0 ? ` (${noLinks} sem vínculo)` : ''}`);
    }
    // Enviar relatório via Telegram (non-blocking)
    const noLinkDetails: string[] = [];
    for (const product of filteredProducts) {
      const linkedAccounts = allAccountsFlat.filter(acc => {
        if (!acc.integrationId) return false;
        if (targetAccountIds && !targetAccountIds.includes(acc.id)) return false;
        const ms = getMarginShipping(acc, product.productType);
        const override = overrideMap.get(`${product.id}-${acc.id}`);
        return ms || override?.priceOverride;
      });
      if (linkedAccounts.length === 0 && noLinks > 0) {
        noLinkDetails.push(`${product.sku} (${product.name.slice(0, 30)})`);
      }
    }
    sendPushReportMut.mutate({
      sent, errors, noLinks, skipped,
      errorDetails: errorDetails.slice(0, 20),
      noLinkDetails: noLinkDetails.slice(0, 20),
      department,
    });
    // Limpar apenas os estados de sucesso após 5s (erro e no_link persistem)
    setTimeout(() => {
      setPushStates(prev => {
        const next = new Map(prev);
        for (const [k, v] of Array.from(next.entries())) {
          if (v === "success") next.delete(k);
        }
        return next;
      });
    }, 5000);
  }, [filteredProducts, allAccountsFlat, overrideMap, FIXED_COLS, pushPriceMut, department, setCellStatusMut]);

  // Contas com integração agrupadas para o dropdown
  const pushableAccountGroups = useMemo(() => {
    return accountGroups.map(g => ({
      label: g.label,
      accounts: g.accounts.filter(a => a.integrationId)
    })).filter(g => g.accounts.length > 0);
  }, [accountGroups]);

  const getCellValue = useCallback((row: number, col: number): string => {
    const product = filteredProducts[row];
    if (!product) return "";
    if (department === "celular") {
      switch (col) {
        case 0: return product.name || "";
        case 1: return (parseFloat(product.blingCostPrice || "0") || 0).toString();
        case 2: return (parseFloat(product.costKit1) || 0).toString();
        case 3: return (parseFloat(product.costKit2 || "0") || 0).toString();
        case 4: return (parseFloat(product.costKit3 || "0") || 0).toString();
        case 5: return (parseFloat(product.costKit4 || "0") || 0).toString();
      }
    } else {
      switch (col) {
        case 0: return product.name || "";
        case 1: return (parseFloat(product.blingCostPrice || "0") || 0).toString();
        case 2: return (parseFloat(product.costKit1) || 0).toString();
      }
    }


    // Price columns
    if (isPriceCol(col)) {
      const accIdx = col - FIXED_COLS;
      const acc = allAccountsFlat[accIdx];
      if (!acc) return "";

      const override = overrideMap.get(`${product.id}-${acc.id}`);
      if (override?.priceOverride) return parseFloat(override.priceOverride).toString();

      const cost = getKitCost(product, acc.kitNumber);
      const ms = getMarginShipping(acc, product.productType);
      if (!ms) return "—"; // Conta não vende esse tipo
      const commission = parseFloat(acc.commission) || 0;
      const price = calcPrice(cost, ms.margin, ms.shipping, commission);
      return price.toString();
    }

    return "";
  }, [filteredProducts, allAccountsFlat, overrideMap, department, FIXED_COLS]);

  // ── Start editing ──
  const startEditing = useCallback((row: number, col: number) => {
    if (!isEditableCol(col) && !isPriceCol(col)) return;
    setSelectedCell({ row, col });
    setEditingCell({ row, col });
    setEditValue(getCellValue(row, col));
  }, [getCellValue]);

  const commitEdit = useCallback(() => {
    if (!editingCell) return;
    const { row, col } = editingCell;
    const oldValue = getCellValue(row, col);
    if (editValue !== oldValue && editValue.trim() !== "" && editValue !== "—") {
      saveCell(row, col, editValue);
    }
    setEditingCell(null);
    setEditValue("");
  }, [editingCell, editValue, getCellValue, saveCell]);

  const cancelEdit = useCallback(() => {
    setEditingCell(null);
    setEditValue("");
  }, []);

  const navigateTo = useCallback((row: number, col: number) => {
    const clampedRow = Math.max(0, Math.min(row, totalRows - 1));
    const clampedCol = Math.max(0, Math.min(col, totalCols - 1));
    setSelectedCell({ row: clampedRow, col: clampedCol });
  }, [totalRows, totalCols]);

  const handleUndo = useCallback(() => {
    if (undoStack.length === 0) return;
    const entry = undoStack[undoStack.length - 1];
    setUndoStack(prev => prev.slice(0, -1));
    setRedoStack(prev => [...prev, entry]);
    saveCell(entry.row, entry.col, entry.oldValue, false);
    toast.info("Desfeito");
  }, [undoStack, saveCell]);

  const handleRedo = useCallback(() => {
    if (redoStack.length === 0) return;
    const entry = redoStack[redoStack.length - 1];
    setRedoStack(prev => prev.slice(0, -1));
    setUndoStack(prev => [...prev, entry]);
    saveCell(entry.row, entry.col, entry.newValue, false);
    toast.info("Refeito");
  }, [redoStack, saveCell]);

  // ── Export to Excel ──
  const handleExportExcel = useCallback(async () => {
    try {
      const XLSX = await import("xlsx");
      const headers: string[] = [];
      if (department === "celular") {
        headers.push("Nome", "Bling", "Kit 1", "Kit 2", "Kit 3", "Kit 4");
      } else {
        headers.push("Nome", "Bling", "Custo");
      }
      allAccountsFlat.forEach(acc => {
        const type = acc.listingType ? ` (${acc.listingType})` : "";
        headers.push(`${acc.name}${type}`);
      });

      const data = filteredProducts.map((_, rowIdx) => {
        const row: (string | number)[] = [];
        for (let c = 0; c < totalCols; c++) {
          const val = getCellValue(rowIdx, c);
          row.push(isPriceCol(c) ? (parseFloat(val) || val) : val);
        }
        return row;
      });

      const ws = XLSX.utils.aoa_to_sheet([headers, ...data]);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, `Preços ${department}`);
      XLSX.writeFile(wb, `tabela_precos_${department}.xlsx`);
      toast.success("Excel exportado!");
    } catch {
      toast.error("Erro ao exportar");
    }
  }, [filteredProducts, allAccountsFlat, totalCols, getCellValue, department]);

  // ── Keyboard handler ──
  const handleTableKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) { e.preventDefault(); handleUndo(); return; }
    if ((e.ctrlKey || e.metaKey) && (e.key === "y" || (e.key === "z" && e.shiftKey))) { e.preventDefault(); handleRedo(); return; }

    if (!selectedCell && !editingCell) return;

    if (editingCell) {
      if (e.key === "Enter") { e.preventDefault(); commitEdit(); const nextRow = Math.min(editingCell.row + 1, totalRows - 1); setSelectedCell({ row: nextRow, col: editingCell.col }); setEditingCell(null); }
      else if (e.key === "Tab") { e.preventDefault(); commitEdit(); const dir = e.shiftKey ? -1 : 1; let nextCol = editingCell.col + dir; let nextRow = editingCell.row; if (nextCol >= totalCols) { nextCol = FIXED_COLS; nextRow = Math.min(nextRow + 1, totalRows - 1); } if (nextCol < FIXED_COLS) { nextCol = totalCols - 1; nextRow = Math.max(nextRow - 1, 0); } setSelectedCell({ row: nextRow, col: nextCol }); setEditingCell(null); }
      else if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
      return;
    }

    if (!selectedCell) return;
    const { row, col } = selectedCell;

    switch (e.key) {
      case "ArrowUp": e.preventDefault(); navigateTo(row - 1, col); break;
      case "ArrowDown": e.preventDefault(); navigateTo(row + 1, col); break;
      case "ArrowLeft": e.preventDefault(); navigateTo(row, col - 1); break;
      case "ArrowRight": e.preventDefault(); navigateTo(row, col + 1); break;
      case "Tab": e.preventDefault(); { const dir = e.shiftKey ? -1 : 1; let nc = col + dir; let nr = row; if (nc >= totalCols) { nc = 0; nr = Math.min(nr + 1, totalRows - 1); } if (nc < 0) { nc = totalCols - 1; nr = Math.max(nr - 1, 0); } navigateTo(nr, nc); } break;
      case "Enter": case "F2": e.preventDefault(); if (isEditableCol(col) || isPriceCol(col)) startEditing(row, col); break;
      case "Delete": case "Backspace": e.preventDefault(); if (isPriceCol(col)) { const product = filteredProducts[row]; const accIdx = col - FIXED_COLS; const acc = allAccountsFlat[accIdx]; if (product && acc) { const override = overrideMap.get(`${product.id}-${acc.id}`); if (override?.priceOverride) { removeOverrideMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id }); flashSaved(row, col); } } } break;
      default: if ((isEditableCol(col) || isPriceCol(col)) && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); setSelectedCell({ row, col }); setEditingCell({ row, col }); setEditValue(e.key); } break;
    }
  }, [selectedCell, editingCell, totalRows, totalCols, FIXED_COLS, navigateTo, startEditing, commitEdit, cancelEdit, filteredProducts, allAccountsFlat, overrideMap, removeOverrideMut, flashSaved, handleUndo, handleRedo]);

  // ── Copy/Paste support ──
  useEffect(() => {
    const handleCopy = (e: ClipboardEvent) => {
      if (!selectedCell || editingCell) return;
      const val = getCellValue(selectedCell.row, selectedCell.col);
      e.clipboardData?.setData("text/plain", val);
      e.preventDefault();
    };

    const handlePaste = (e: ClipboardEvent) => {
      if (!selectedCell || editingCell) return;
      if (!isEditableCol(selectedCell.col) && !isPriceCol(selectedCell.col)) return;
      const text = e.clipboardData?.getData("text/plain")?.trim();
      if (!text) return;
      e.preventDefault();

      const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
      if (lines.length > 1) {
        lines.forEach((line, lineIdx) => {
          const targetRow = selectedCell.row + lineIdx;
          if (targetRow >= totalRows) return;
          const cells = line.split("\t");
          cells.forEach((cellVal, cellIdx) => {
            const targetCol = selectedCell.col + cellIdx;
            if (targetCol >= totalCols) return;
            if (!isEditableCol(targetCol) && !isPriceCol(targetCol)) return;
            const val = cellVal.trim();
            if (val) saveCell(targetRow, targetCol, val);
          });
        });
        toast.success(`Colado ${lines.length} linhas`);
      } else {
        const cells = text.split("\t");
        cells.forEach((cellVal, cellIdx) => {
          const targetCol = selectedCell.col + cellIdx;
          if (targetCol >= totalCols) return;
          if (!isEditableCol(targetCol) && !isPriceCol(targetCol)) return;
          const val = cellVal.trim();
          if (val) saveCell(selectedCell.row, targetCol, val);
        });
      }
    };

    document.addEventListener("copy", handleCopy);
    document.addEventListener("paste", handlePaste);
    return () => {
      document.removeEventListener("copy", handleCopy);
      document.removeEventListener("paste", handlePaste);
    };
  }, [selectedCell, editingCell, getCellValue, saveCell, totalRows, totalCols]);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-muted-foreground">
          Carregando tabela de preços...
        </CardContent>
      </Card>
    );
  }

  if (activeAccounts.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Settings2 className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
          <h3 className="text-lg font-semibold mb-2">Configure suas contas primeiro</h3>
          <p className="text-muted-foreground">Adicione as contas de venda na aba "Contas" para começar.</p>
        </CardContent>
      </Card>
    );
  }

  if (filteredProducts.length === 0 && products.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Upload className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
          <h3 className="text-lg font-semibold mb-2">Adicione seus produtos</h3>
          <p className="text-muted-foreground">Importe ou cadastre produtos na aba "Produtos".</p>
        </CardContent>
      </Card>
    );
  }

  const colW = "min-w-[80px]";

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Buscar por SKU ou nome..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
        <Button variant="outline" size="sm" onClick={handleUndo} disabled={undoStack.length === 0} title="Desfazer (Ctrl+Z)">
          <Undo2 className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={handleRedo} disabled={redoStack.length === 0} title="Refazer (Ctrl+Y)">
          <Redo2 className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="sm" onClick={handleExportExcel} title="Exportar Excel">
          <Download className="h-4 w-4 mr-1" />
          Excel
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => syncBlingCostsMut.mutate()}
          disabled={syncBlingCostsMut.isPending}
          title="Atualizar custos do Bling"
          className="gap-1.5 text-green-700 border-green-300 hover:bg-green-50"
        >
          {syncBlingCostsMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Custo Bling
        </Button>
        {compareMode && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setCompareProductIds(new Set());
              setActualPriceMap({});
            }}
            title="Limpar todas as comparações"
            className="gap-1.5 text-purple-600 border-purple-300 hover:bg-purple-50"
          >
            <X className="h-4 w-4" />
            Limpar ({compareProductIds.size})
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="default"
              size="sm"
              disabled={isBulkPushing}
              title="Enviar preços para as plataformas"
              className="gap-1.5 bg-blue-600 hover:bg-blue-700"
            >
              {isBulkPushing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {isBulkPushing && bulkPushLabel ? bulkPushLabel : "Enviar"}
              <ChevronDown className="h-3 w-3 ml-0.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-80 overflow-y-auto w-56">
            <DropdownMenuItem onClick={() => { setBulkPushLabel("Todas"); pushPricesForAccounts(); }}>
              <Send className="h-3.5 w-3.5 mr-2" />
              Enviar Todas as Contas
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {pushableAccountGroups.map((group) => (
              <Fragment key={group.label}>
                <DropdownMenuLabel className="text-xs text-muted-foreground">{group.label}</DropdownMenuLabel>
                {group.accounts.map(acc => (
                  <DropdownMenuItem
                    key={acc.id}
                    onClick={() => {
                      setBulkPushLabel(acc.name);
                      pushPricesForAccounts([acc.id]);
                    }}
                  >
                    <span className="truncate">{acc.name}</span>
                    <Badge variant="outline" className="ml-auto text-[10px] px-1">{acc.listingType || acc.platform}</Badge>
                  </DropdownMenuItem>
                ))}
              </Fragment>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <span className="text-xs text-muted-foreground">
          {filteredProducts.length} produto(s) · {activeAccounts.length} conta(s) · Fórmula: ((Custo × Margem) + Frete + Custo) / (1 - Comissão)
        </span>
      </div>

      {/* Negative margin banner */}
      {(() => {
        let negCount = 0;
        for (const product of filteredProducts) {
          for (const acc of activeAccounts) {
            const ms = getMarginShipping(acc, product.productType);
            if (!ms) continue;
            const cost = getKitCost(product, acc.kitNumber);
            if (cost <= 0) continue;
            const commission = parseFloat(acc.commission) || 0;
            const price = calcPrice(cost, ms.margin, ms.shipping, commission);
            if (price < cost + ms.shipping) negCount++;
          }
        }
        if (negCount === 0) return null;
        return (
          <div className="flex items-center gap-2 px-3 py-2 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg text-sm">
            <AlertTriangle className="h-4 w-4 text-red-600 flex-shrink-0" />
            <span className="text-red-700 dark:text-red-300">
              <strong>{negCount}</strong> combinações com margem negativa (preço {'<'} custo + frete)
            </span>
          </div>
        );
      })()}

      {/* Excel-like spreadsheet */}
      <div
        ref={tableRef}
        className="border rounded-lg overflow-auto max-h-[calc(100vh-280px)] focus:outline-none"
        style={{ fontSize: "13px" }}
        tabIndex={0}
        onKeyDown={handleTableKeyDown}
        onClick={(e) => {
          if (e.target === tableRef.current) {
            setSelectedCell(null);
            setEditingCell(null);
          }
        }}
      >
        <table className="border-collapse" style={{ minWidth: "100%", lineHeight: "1.4" }}>
          <thead className="sticky top-0 z-20">
            {/* ROW 1: Platform group headers */}
            <tr className="bg-gray-200 dark:bg-gray-800">
              <th
                colSpan={1}
                rowSpan={3}
                className="border border-gray-300 dark:border-gray-600 px-2 py-1 text-left font-bold sticky left-0 bg-gray-200 dark:bg-gray-800 z-30"
                style={{ minWidth: department === "celular" ? 200 : 200 }}
              >
                <div className="text-xs font-bold">{department === "celular" ? "Celular" : department === "mala" ? "Mala" : department === "eletro" ? "Eletro" : "Catálogo ML"}</div>
                <div className="text-[10px] text-muted-foreground">produtos</div>
              </th>
              <th
                colSpan={department === "celular" ? 5 : 2}
                rowSpan={2}
                className="border border-gray-300 dark:border-gray-600 px-2 py-1.5 text-center bg-gray-100 dark:bg-gray-700 sticky z-30"
                style={{ left: 200 }}
              >
                <div className="text-xs text-muted-foreground font-medium">custos</div>
              </th>
              {accountGroups.map((group, gi) => (
                <th
                  key={gi}
                  colSpan={group.accounts.length}
                  className={`border border-gray-300 dark:border-gray-600 px-2 py-1.5 text-center font-bold ${
                    group.label.includes("Shopee") ? "bg-orange-50 dark:bg-orange-900/20" :
                    group.label.includes("Amazon") ? "bg-yellow-50 dark:bg-yellow-900/20" :
                    group.label.includes("Temu") ? "bg-purple-50 dark:bg-purple-900/20" :
                    group.label.includes("AliExpress") ? "bg-red-50 dark:bg-red-900/20" :
                    group.label.includes("TikTok") ? "bg-pink-50 dark:bg-pink-900/20" :
                    "bg-blue-50 dark:bg-blue-900/20"
                  }`}
                  style={{ borderLeftWidth: '3px', borderLeftColor: '#6b7280' }}
                >
                  <div className="text-xs font-semibold whitespace-nowrap">{group.label}</div>
                </th>
              ))}
            </tr>

            {/* ROW 2: Account names + observation */}
            <tr className="bg-gray-100 dark:bg-gray-700">
              {accountGroups.flatMap(group =>
                group.accounts.map((acc, accIdxInGroup) => (
                  <th
                    key={acc.id}
                    className={`border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center ${colW} ${
                      acc.platform === "shopee" ? "bg-orange-50 dark:bg-orange-900/10" :
                      acc.platform === "amazon" ? "bg-yellow-50 dark:bg-yellow-900/10" :
                      acc.platform === "temu" ? "bg-purple-50 dark:bg-purple-900/10" :
                      acc.platform === "aliexpress" ? "bg-red-50 dark:bg-red-900/10" :
                      acc.platform === "tiktok" ? "bg-pink-50 dark:bg-pink-900/10" :
                      "bg-blue-50 dark:bg-blue-900/10"
                    }`}
                    style={accIdxInGroup === 0 ? { borderLeftWidth: '3px', borderLeftColor: '#6b7280' } : undefined}
                  >
                    <div className="text-xs font-semibold whitespace-nowrap">{acc.name}</div>
                    {(["observation", "observation2", "observation3"] as const).map((field, idx) => {
                      const key = `${acc.id}-${field}`;
                      const val = acc[field];
                      return editingObsId === key ? (
                        <input
                          key={field}
                          ref={obsInputRef}
                          type="text"
                          value={obsValue}
                          onChange={e => setObsValue(e.target.value)}
                          onBlur={() => commitObs(acc.id, field)}
                          onKeyDown={e => {
                            if (e.key === "Enter") { e.preventDefault(); commitObs(acc.id, field); }
                            if (e.key === "Escape") { e.preventDefault(); setEditingObsId(null); }
                          }}
                          className="w-full text-[9px] border border-blue-400 rounded px-1 py-0.5 mt-0.5 bg-white dark:bg-gray-900 text-foreground outline-none text-center"
                          placeholder={`obs${idx + 1}...`}
                        />
                      ) : (
                        <div
                          key={field}
                          className={`text-[9px] mt-0.5 cursor-pointer truncate max-w-[90px] mx-auto ${
                            val ? "text-amber-600 dark:text-amber-400 font-medium" : "text-muted-foreground/50 italic hover:text-muted-foreground"
                          }`}
                          title={val || `Clique para adicionar obs${idx + 1}`}
                          onClick={(e) => { e.stopPropagation(); setEditingObsId(key); setObsValue(val || ""); }}
                        >
                          {val || `obs${idx + 1}`}
                        </div>
                      );
                    })}
                  </th>
                ))
              )}
            </tr>

            {/* ROW 3: Listing type + cost headers */}
            <tr className="bg-gray-50 dark:bg-gray-750">
              {/* Bling cost header - always first */}
              <th className="border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center bg-green-50 dark:bg-green-900/20 min-w-[56px] sticky z-30" style={{ left: 200 }}>
                <div className="text-[11px] text-green-700 dark:text-green-400 font-medium">bling</div>
              </th>
              {department === "celular" ? (
                <>
                  <th className="border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center bg-gray-100 dark:bg-gray-700 min-w-[56px] sticky z-30" style={{ left: 256 }}>
                    <div className="text-[11px] text-muted-foreground font-medium">kit 1</div>
                  </th>
                  <th className="border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center bg-gray-100 dark:bg-gray-700 min-w-[56px] sticky z-30" style={{ left: 312 }}>
                    <div className="text-[11px] text-muted-foreground font-medium">kit 2</div>
                  </th>
                  <th className="border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center bg-gray-100 dark:bg-gray-700 min-w-[56px] sticky z-30" style={{ left: 368 }}>
                    <div className="text-[11px] text-muted-foreground font-medium">kit 3</div>
                  </th>
                  <th className="border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center bg-gray-100 dark:bg-gray-700 min-w-[56px] sticky z-30" style={{ left: 424 }}>
                    <div className="text-[11px] text-muted-foreground font-medium">kit 4</div>
                  </th>
                </>
              ) : (
                <th className="border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center bg-gray-100 dark:bg-gray-700 min-w-[56px] sticky z-30" style={{ left: 256 }}>
                  <div className="text-[11px] text-muted-foreground font-medium">custo</div>
                </th>
              )}
              {accountGroups.flatMap(group =>
                group.accounts.map((acc, accIdxInGroup) => {
                  const typeLabel = acc.listingType || acc.platform;
                  return (
                    <th
                      key={acc.id}
                      className={`border border-gray-300 dark:border-gray-600 px-1.5 py-1 text-center ${colW} ${
                        acc.platform === "shopee" ? "bg-orange-100 dark:bg-orange-900/15" :
                        acc.platform === "amazon" ? "bg-yellow-100 dark:bg-yellow-900/15" :
                        acc.platform === "temu" ? "bg-purple-100 dark:bg-purple-900/15" :
                        acc.platform === "aliexpress" ? "bg-red-100 dark:bg-red-900/15" :
                        acc.platform === "tiktok" ? "bg-pink-100 dark:bg-pink-900/15" :
                        acc.listingType === "ml premium" ? "bg-blue-100 dark:bg-blue-900/20" :
                        "bg-blue-50 dark:bg-blue-900/10"
                      }`}
                      style={accIdxInGroup === 0 ? { borderLeftWidth: '3px', borderLeftColor: '#6b7280' } : undefined}
                    >
                      <div className="text-[11px] text-muted-foreground whitespace-nowrap">{typeLabel}</div>
                    </th>
                  );
                })
              )}
            </tr>
          </thead>

          {/* ── DATA ROWS ── */}
          <tbody>
            {filteredProducts.map((product, rowIdx) => {
              const rowBg = rowIdx % 2 === 0 ? "bg-white dark:bg-gray-900" : "bg-gray-50 dark:bg-gray-850";

              return (
                <tr key={product.id} className={`${rowBg} hover:bg-yellow-50/50 dark:hover:bg-yellow-900/5 transition-colors group`}>
                  {/* Nome - sticky + editável */}
                  <InlineEditCell
                    row={rowIdx} col={0}
                    selectedCell={selectedCell} editingCell={editingCell} savedCells={savedCells}
                    editValue={editValue} setEditValue={setEditValue} inputRef={inputRef}
                    onClickCell={startEditing} onCommit={commitEdit} onCancel={cancelEdit}
                    className={`sticky left-0 z-10 max-w-[200px] ${rowBg}`}
                    style={{ minWidth: 200 }}
                    displayClassName="text-left"
                    inputType="text"
                  >
                    <div className="flex items-center gap-1">
                      <span title={product.name} className="truncate flex-1">{product.name}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleCompetitorSearch(product); }}
                        className={`flex-shrink-0 p-0.5 rounded transition-colors ${
                          competitorPopoverId === product.id
                            ? 'text-purple-600 bg-purple-100 dark:bg-purple-900/40'
                            : 'text-gray-400 hover:text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20 opacity-0 group-hover:opacity-100'
                        }`}
                        title={`Comparar pre\u00e7os de "${product.name}" com concorrentes`}
                        disabled={competitorLoading === product.id}
                      >
                        {competitorLoading === product.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Eye className="h-3 w-3" />}
                      </button>
                    </div>
                  </InlineEditCell>

                  {/* Bling cost - read-only */}
                  <td
                    className={`border border-gray-200 dark:border-gray-700 px-1.5 py-1 text-center text-xs sticky z-10 ${rowBg}`}
                    style={{ left: 200, backgroundColor: rowIdx % 2 === 0 ? '#f0fdf4' : '#ecfdf5' }}
                  >
                    <span className="font-bold text-green-700 dark:text-green-400">
                      {product.blingCostPrice && parseFloat(product.blingCostPrice) > 0 ? parseFloat(product.blingCostPrice).toFixed(0) : "—"}
                    </span>
                  </td>
                  {/* Custos editáveis - sticky */}
                  {department === "celular" ? (
                    [2, 3, 4, 5].map(c => (
                      <InlineEditCell
                        key={c} row={rowIdx} col={c}
                        selectedCell={selectedCell} editingCell={editingCell} savedCells={savedCells}
                        editValue={editValue} setEditValue={setEditValue} inputRef={inputRef}
                        onClickCell={startEditing} onCommit={commitEdit} onCancel={cancelEdit}
                        className={`text-center sticky z-10 ${rowBg}`}
                        style={{ left: 200 + (c - 1) * 56, backgroundColor: rowIdx % 2 === 0 ? '#fafafa' : '#f5f5f5' }}
                        displayClassName={c === 2 ? "font-bold text-blue-700 dark:text-blue-400" : "text-muted-foreground"}
                      >
                        {(parseFloat(getCellValue(rowIdx, c)) || 0).toFixed(0)}
                      </InlineEditCell>
                    ))
                  ) : (
                    <InlineEditCell
                      row={rowIdx} col={2}
                      selectedCell={selectedCell} editingCell={editingCell} savedCells={savedCells}
                      editValue={editValue} setEditValue={setEditValue} inputRef={inputRef}
                      onClickCell={startEditing} onCommit={commitEdit} onCancel={cancelEdit}
                      className={`text-center sticky z-10 ${rowBg}`}
                      style={{ left: 256, backgroundColor: rowIdx % 2 === 0 ? '#fafafa' : '#f5f5f5' }}
                      displayClassName="font-bold text-blue-700 dark:text-blue-400"
                    >
                      {(parseFloat(product.costKit1) || 0).toFixed(0)}
                    </InlineEditCell>
                  )}

                  {/* Price cells per account */}
                  {allAccountsFlat.map((acc, accIdx) => {
                    const colIdx = FIXED_COLS + accIdx;
                    const override = overrideMap.get(`${product.id}-${acc.id}`);
                    const ms = getMarginShipping(acc, product.productType);
                    const hasOverride = !!override?.priceOverride;
                    const isInvalid = !ms && !hasOverride; // Conta não vende esse tipo

                    let displayPrice: string;
                    if (hasOverride) {
                      displayPrice = parseFloat(override!.priceOverride!).toFixed(0);
                    } else if (isInvalid) {
                      displayPrice = "—";
                    } else {
                      const cost = getKitCost(product, acc.kitNumber);
                      const commission = parseFloat(acc.commission) || 0;
                      displayPrice = calcPrice(cost, ms!.margin, ms!.shipping, commission).toFixed(0);
                    }

                    // Status persistido do banco
                    const persistedStatus = override?.cellStatus;

                    // Detectar margem negativa: preço calculado < custo + frete
                    let isNegativeMargin = false;
                    if (!isInvalid && displayPrice !== "—") {
                      const cost = getKitCost(product, acc.kitNumber);
                      const ms2 = getMarginShipping(acc, product.productType);
                      const shipping = ms2 ? ms2.shipping : 0;
                      const priceNum = parseFloat(displayPrice);
                      if (!isNaN(priceNum) && priceNum < cost + shipping) {
                        isNegativeMargin = true;
                      }
                    }

                    let cellBg = "";
                    if (persistedStatus === "NA") cellBg = "bg-gray-200 dark:bg-gray-700";
                    else if (persistedStatus === "SV") cellBg = "bg-amber-100 dark:bg-amber-900/30";
                    else if (persistedStatus === "error") cellBg = "bg-red-50 dark:bg-red-900/20";
                    else if (persistedStatus === "no_link") cellBg = "bg-yellow-50 dark:bg-yellow-900/20";
                    else if (isNegativeMargin) cellBg = "bg-red-100 dark:bg-red-900/40";
                    else if (isInvalid) cellBg = "bg-gray-100 dark:bg-gray-800";
                    else if (hasOverride) cellBg = "bg-orange-50 dark:bg-orange-900/20";

                    const pushKey = cellKey(rowIdx, colIdx);
                    const pushState = pushStates.get(pushKey);
                    let pushBg = "";
                    if (pushState === "pushing") pushBg = "!bg-blue-50 dark:!bg-blue-900/30";
                    else if (pushState === "success") pushBg = "!bg-green-50 dark:!bg-green-900/30";
                    else if (pushState === "error") pushBg = "!bg-red-50 dark:!bg-red-900/30";
                    else if (pushState === "no_link") pushBg = "!bg-yellow-50 dark:!bg-yellow-900/30";

                    // Determinar se mostra NA/SV ou o preço
                    const showNA = persistedStatus === "NA";
                    const showSV = persistedStatus === "SV";

                    const isGroupStart = groupStartIndices.has(accIdx);

                    // Compare mode: get actual price (per-product)
                    const isComparing = compareProductIds.has(product.id);
                    const actualPrice = isComparing ? getActualPrice(product, acc) : null;
                    const priceNum = parseFloat(displayPrice);
                    const priceDiff = (actualPrice !== null && !isNaN(priceNum) && !isInvalid && !showNA && !showSV)
                      ? Math.round(actualPrice - priceNum)
                      : null;

                    // Override cellBg if compare mode shows mismatch
                    let compareBg = "";
                    if (isComparing && priceDiff !== null && priceDiff !== 0) {
                      compareBg = priceDiff < 0 ? "!bg-purple-50 dark:!bg-purple-900/20" : "!bg-cyan-50 dark:!bg-cyan-900/20";
                    }

                    return (
                      <InlineEditCell
                        key={acc.id}
                        row={rowIdx} col={colIdx}
                        selectedCell={selectedCell} editingCell={editingCell} savedCells={savedCells}
                        editValue={editValue} setEditValue={setEditValue} inputRef={inputRef}
                        onClickCell={(showNA || showSV) ? () => { setClearStatusCell(prev => prev?.productId === product.id && prev?.accountId === acc.id ? null : { productId: product.id, accountId: acc.id }); } : (isInvalid && !persistedStatus) ? () => setSelectedCell({ row: rowIdx, col: colIdx }) : startEditing}
                        onCommit={commitEdit} onCancel={cancelEdit}
                        className={`text-center ${cellBg} ${pushBg} ${compareBg} group/cell`}
                        style={isGroupStart ? { borderLeftWidth: '3px', borderLeftColor: '#6b7280' } : undefined}
                        displayClassName={showNA ? "text-gray-500 font-semibold" : showSV ? "text-amber-700 dark:text-amber-400 font-semibold" : isInvalid ? "text-gray-400" : isNegativeMargin ? "text-red-700 dark:text-red-400 font-bold" : hasOverride ? "text-orange-700 dark:text-orange-400 font-semibold" : ""}
                      >
                        <span className="inline-flex items-center gap-0.5">
                          {showNA ? "NA" : showSV ? "SV" : displayPrice}
                          {pushState === "pushing" && <Loader2 className="h-3 w-3 animate-spin text-blue-500" />}
                          {pushState === "success" && <CheckCircle2 className="h-3 w-3 text-green-500" />}
                          {pushState === "error" && <AlertCircle className="h-3 w-3 text-red-500" />}
                          {pushState === "no_link" && <AlertCircle className="h-3 w-3 text-yellow-500" />}
                          {/* Botão NA para células com erro (pushState ou persistido) */}
                          {(pushState === "error" || (!pushState && persistedStatus === "error")) && (
                            <button
                              onClick={(e) => { e.stopPropagation(); setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "NA" }); }}
                              className="ml-0.5 px-1 py-0 text-[9px] font-bold text-red-600 bg-red-100 dark:bg-red-900/40 hover:bg-red-200 dark:hover:bg-red-800/60 rounded cursor-pointer"
                              title="Marcar como Não Anunciar"
                            >
                              NA
                            </button>
                          )}
                          {/* Botão SV para células sem vínculo (pushState ou persistido) */}
                          {(pushState === "no_link" || (!pushState && persistedStatus === "no_link")) && (
                            <button
                              onClick={(e) => { e.stopPropagation(); setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: "SV" }); }}
                              className="ml-0.5 px-1 py-0 text-[9px] font-bold text-yellow-700 bg-yellow-100 dark:bg-yellow-900/40 hover:bg-yellow-200 dark:hover:bg-yellow-800/60 rounded cursor-pointer"
                              title="Marcar como Sem Vínculo"
                            >
                              SV
                            </button>
                          )}
                          {/* Popup para limpar NA/SV - aparece ao clicar na célula */}
                          {!pushState && (showNA || showSV) && clearStatusCell?.productId === product.id && clearStatusCell?.accountId === acc.id && (
                            <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 z-50 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded shadow-lg p-1 whitespace-nowrap">
                              <button
                                onClick={(e) => { e.stopPropagation(); setCellStatusMut.mutate({ pricingProductId: product.id, pricingAccountId: acc.id, cellStatus: null }); setClearStatusCell(null); }}
                                className="px-2 py-1 text-xs font-semibold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 rounded cursor-pointer flex items-center gap-1"
                              >
                                <X className="h-3 w-3" /> Limpar {showNA ? "NA" : "SV"}
                              </button>
                            </div>
                          )}
                          {/* Botão de push normal */}
                          {!isInvalid && acc.integrationId && !pushState && !showNA && !showSV && !persistedStatus && (
                            <button
                              onClick={(e) => { e.stopPropagation(); pushSinglePrice(rowIdx, colIdx); }}
                              className="opacity-0 group-hover/cell:opacity-100 transition-opacity ml-0.5 p-0 hover:text-blue-600"
                              title={`Enviar R$${displayPrice} para ${acc.platform}`}
                            >
                              <Send className="h-3 w-3 text-blue-400 hover:text-blue-600" />
                            </button>
                          )}
                        </span>
                        {/* Compare mode: show actual price and diff */}
                        {isComparing && !showNA && !showSV && !isInvalid && (
                          <div className="text-[9px] leading-tight mt-0.5">
                            {actualPrice !== null ? (
                              <>
                                <span className="text-gray-500">Atual: R${Math.round(actualPrice)}</span>
                                {priceDiff !== null && priceDiff !== 0 && (
                                  <span className={`ml-0.5 font-bold ${priceDiff < 0 ? 'text-red-600' : 'text-green-600'}`}>
                                    {priceDiff > 0 ? '+' : ''}{priceDiff}
                                  </span>
                                )}
                                {priceDiff === 0 && <span className="ml-0.5 text-green-600 font-bold">OK</span>}
                              </>
                            ) : (
                              <span className="text-gray-400 italic">s/ anuncio</span>
                            )}
                          </div>
                        )}
                      </InlineEditCell>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Competitor comparison floating card */}
      {competitorPopoverId !== null && (() => {
        const product = filteredProducts.find(p => p.id === competitorPopoverId);
        if (!product) return null;
        return (
          <div className="fixed inset-0 z-[9999]" onClick={() => setCompetitorPopoverId(null)}>
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[380px] max-h-[80vh] overflow-y-auto bg-white dark:bg-gray-900 rounded-xl shadow-2xl border-2 border-purple-300 dark:border-purple-700" onClick={(e) => e.stopPropagation()}>
              {competitorLoading === product.id ? (
                <div className="p-6 text-center">
                  <Loader2 className="h-6 w-6 animate-spin mx-auto text-purple-500 mb-2" />
                  <p className="text-sm text-muted-foreground">Buscando concorrentes...</p>
                </div>
              ) : competitorData[product.id] ? (
                <div className="text-xs">
                  {/* Header */}
                  <div className="px-4 py-3 bg-purple-50 dark:bg-purple-900/30 border-b flex items-center gap-2 rounded-t-xl">
                    <BarChart3 className="h-4 w-4 text-purple-600 flex-shrink-0" />
                    <span className="font-semibold text-purple-800 dark:text-purple-300 truncate flex-1 text-sm">{product.name}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setCompetitorData(prev => { const n = { ...prev }; delete n[product.id]; return n; });
                        setCompetitorLoading(product.id);
                        searchCompetitorMut.mutateAsync({ productName: product.name, productSku: product.sku })
                          .then(res => { setCompetitorData(prev => ({ ...prev, [product.id]: res })); })
                          .catch(() => {})
                          .finally(() => setCompetitorLoading(null));
                      }}
                      className="p-1 hover:bg-purple-200 dark:hover:bg-purple-800 rounded"
                      title="Atualizar busca"
                    >
                      <RefreshCw className="h-3.5 w-3.5 text-purple-600" />
                    </button>
                    <button
                      onClick={() => setCompetitorPopoverId(null)}
                      className="p-1 hover:bg-purple-200 dark:hover:bg-purple-800 rounded"
                      title="Fechar"
                    >
                      <X className="h-3.5 w-3.5 text-purple-600" />
                    </button>
                  </div>
                  {/* Comparison rows */}
                  <div className="divide-y">
                    {["ml classico", "ml premium"].map(lt => {
                      const data = competitorData[product.id]?.grouped[lt];
                      const ownPrice = getOwnPriceForListingType(product, lt);
                      const label = lt === "ml classico" ? "ML Cl\u00e1ssico" : "ML Premium";
                      const labelColor = lt === "ml classico" ? "text-blue-700 dark:text-blue-400" : "text-indigo-700 dark:text-indigo-400";
                      return (
                        <div key={lt} className="px-4 py-3">
                          <div className="flex items-center justify-between mb-1.5">
                            <span className={`font-bold text-sm ${labelColor}`}>{label}</span>
                            {data && data.count > 0 && (
                              <span className="text-[10px] text-muted-foreground">{data.count} an\u00fancios</span>
                            )}
                          </div>
                          {!data || data.count === 0 ? (
                            <p className="text-muted-foreground italic">Nenhum resultado encontrado</p>
                          ) : (
                            <div className="space-y-1.5">
                              {/* Price stats */}
                              <div className="grid grid-cols-3 gap-1.5">
                                <div className="bg-green-50 dark:bg-green-900/20 rounded px-2 py-1.5 text-center">
                                  <div className="text-[10px] text-muted-foreground">M\u00ednimo</div>
                                  <div className="font-bold text-green-700 dark:text-green-400">R${data.minPrice.toLocaleString('pt-BR')}</div>
                                </div>
                                <div className="bg-blue-50 dark:bg-blue-900/20 rounded px-2 py-1.5 text-center">
                                  <div className="text-[10px] text-muted-foreground">Mediana</div>
                                  <div className="font-bold text-blue-700 dark:text-blue-400">R${data.medianPrice.toLocaleString('pt-BR')}</div>
                                </div>
                                <div className="bg-red-50 dark:bg-red-900/20 rounded px-2 py-1.5 text-center">
                                  <div className="text-[10px] text-muted-foreground">M\u00e1ximo</div>
                                  <div className="font-bold text-red-700 dark:text-red-400">R${data.maxPrice.toLocaleString('pt-BR')}</div>
                                </div>
                              </div>
                              {/* Own price comparison */}
                              {ownPrice !== null && (
                                <div className={`flex items-center gap-2 px-2 py-1.5 rounded ${
                                  ownPrice < data.minPrice ? 'bg-green-100 dark:bg-green-900/30' :
                                  ownPrice > data.maxPrice ? 'bg-red-100 dark:bg-red-900/30' :
                                  'bg-amber-100 dark:bg-amber-900/30'
                                }`}>
                                  {ownPrice < data.minPrice ? (
                                    <TrendingDown className="h-3.5 w-3.5 text-green-600 flex-shrink-0" />
                                  ) : ownPrice > data.maxPrice ? (
                                    <TrendingUp className="h-3.5 w-3.5 text-red-600 flex-shrink-0" />
                                  ) : (
                                    <Check className="h-3.5 w-3.5 text-amber-600 flex-shrink-0" />
                                  )}
                                  <span className="font-semibold">Seu pre\u00e7o: R${Math.round(ownPrice).toLocaleString('pt-BR')}</span>
                                  {ownPrice < data.minPrice && (
                                    <span className="text-green-700 dark:text-green-400 font-bold ml-auto">R${Math.round(data.minPrice - ownPrice)} abaixo</span>
                                  )}
                                  {ownPrice > data.maxPrice && (
                                    <span className="text-red-700 dark:text-red-400 font-bold ml-auto">R${Math.round(ownPrice - data.maxPrice)} acima</span>
                                  )}
                                  {ownPrice >= data.minPrice && ownPrice <= data.maxPrice && (
                                    <span className="text-amber-700 dark:text-amber-400 font-bold ml-auto">Na faixa</span>
                                  )}
                                </div>
                              )}
                              {/* Top results */}
                              {data.topResults.length > 0 && (
                                <div className="mt-1.5">
                                  <div className="text-[10px] text-muted-foreground mb-1">Top concorrentes:</div>
                                  {data.topResults.map((r: any, i: number) => (
                                    <a
                                      key={i}
                                      href={r.permalink}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="flex items-center gap-1.5 py-0.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded px-1.5 -mx-1 transition-colors"
                                      onClick={(e) => e.stopPropagation()}
                                    >
                                      <span className="truncate flex-1 text-[11px]" title={r.title}>{r.title}</span>
                                      <span className="font-bold text-[11px] whitespace-nowrap">R${r.price.toLocaleString('pt-BR')}</span>
                                      <ExternalLink className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                                    </a>
                                  ))}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {/* Footer */}
                  <div className="px-4 py-2 bg-gray-50 dark:bg-gray-800/50 border-t text-[10px] text-muted-foreground rounded-b-xl">
                    Busca: "{competitorData[product.id]?.query}" · {competitorData[product.id]?.totalResults} resultados
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        );
      })()}

      {/* Footer info */}
      <div className="text-xs text-muted-foreground space-y-1 px-1">
        <p>
          <strong>Clique</strong> na célula para editar · <strong>Setas</strong> para mover · <strong>Tab</strong> avança · <strong>Esc</strong> cancela · <strong>Delete</strong> remove override · <strong>Ctrl+Z/Y</strong> desfazer/refazer · <strong>Ctrl+C/V</strong> copiar/colar
        </p>
        <p>
          <span className="text-orange-600">■</span> override manual · <span className="text-gray-400">—</span> conta não vende esse tipo ·
          <span className="text-red-500">■</span> erro no push · <span className="text-yellow-500">■</span> sem vínculo ·
          <span className="font-bold text-gray-500">NA</span> = Não Anunciar · <span className="font-bold text-amber-700">SV</span> = Sem Vínculo ·
          <span className="text-red-700 font-bold">■</span> margem negativa (preço {'<'} custo + frete)
        </p>
        <p className="text-purple-600 dark:text-purple-400">
          <Eye className="inline h-3 w-3 mr-0.5" />
          Clique no <strong>olho</strong> ao lado do nome do produto para comparar preços com marketplace
          {compareMode && <> · <strong>{compareProductIds.size}</strong> produto(s) comparando</>}
        </p>
        {compareMode && (
          <p>
            <span className="text-purple-600">■</span> preço na plataforma menor que calculado ·
            <span className="text-cyan-600">■</span> preço na plataforma maior que calculado ·
            <span className="text-green-600 font-bold">OK</span> = preço igual ·
            <span className="text-gray-400 italic">s/ anuncio</span> = SKU não encontrado na plataforma
          </p>
        )}
        <p className="text-blue-600 dark:text-blue-400">
          <Send className="inline h-3 w-3 mr-0.5" />
          Passe o mouse sobre um preço para ver o botão de enviar · <strong>Enviar</strong> permite escolher uma conta específica ou todas
        </p>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// ── InlineEditCell ──
// ══════════════════════════════════════════════════════════════════

function InlineEditCell({
  row, col, selectedCell, editingCell, savedCells,
  editValue, setEditValue, inputRef,
  className, displayClassName, children,
  onClickCell, onCommit, onCancel,
  inputType, style,
}: {
  row: number; col: number;
  selectedCell: CellCoord | null;
  editingCell: CellCoord | null;
  savedCells: Set<string>;
  editValue: string;
  setEditValue: (v: string) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
  className?: string;
  displayClassName?: string;
  children: React.ReactNode;
  onClickCell: (r: number, c: number) => void;
  onCommit: () => void;
  onCancel: () => void;
  inputType?: "text" | "decimal";
  style?: React.CSSProperties;
}) {
  const isSelected = selectedCell?.row === row && selectedCell?.col === col;
  const isEditing = editingCell?.row === row && editingCell?.col === col;
  const isSaved = savedCells.has(cellKey(row, col));

  return (
    <td
      className={`border border-gray-300 dark:border-gray-600 px-1.5 py-1 cursor-pointer select-none relative ${className || ""} ${
        isEditing
          ? "ring-2 ring-blue-600 ring-inset z-10 !bg-white dark:!bg-gray-900"
          : isSelected
            ? "ring-2 ring-blue-500 ring-inset z-10 bg-blue-50/50 dark:bg-blue-900/30"
            : ""
      } ${isSaved && !isEditing ? "!bg-green-50 dark:!bg-green-900/20" : ""}`}
      style={style}
      onClick={() => {
        if (!isEditing) onClickCell(row, col);
      }}
    >
      {isEditing ? (
        <input
          ref={inputRef}
          type="text"
          inputMode={inputType === "text" ? "text" : "decimal"}
          value={editValue}
          onChange={e => setEditValue(e.target.value)}
          onBlur={() => onCommit()}
          onKeyDown={e => {
            if (e.key === "Enter") { e.preventDefault(); onCommit(); }
            if (e.key === "Escape") { e.preventDefault(); onCancel(); }
            if (e.key === "Tab") { e.preventDefault(); onCommit(); }
            if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
              e.stopPropagation();
            }
          }}
          className={`w-full text-xs ${inputType === "text" ? "text-left" : "text-center"} border-0 outline-none bg-transparent font-mono p-0 m-0`}
          style={{ minWidth: "44px" }}
        />
      ) : (
        <div className={`text-xs font-mono ${displayClassName || ""} relative`}>
          {isSaved && (
            <span className="absolute -top-0.5 -right-0.5">
              <Check className="h-3 w-3 text-green-500" />
            </span>
          )}
          {children}
        </div>
      )}
    </td>
  );
}

// ══════════════════════════════════════════════════════════════════
// ── ACCOUNTS TABLE ──
// ══════════════════════════════════════════════════════════════════

function AccountsTable({ department, accounts }: { department: Department; accounts: PricingAccount[] }) {
  const utils = trpc.useUtils();
  const updateMut = trpc.pricing.updateAccount.useMutation({
    onSuccess: () => utils.pricing.getAccounts.invalidate(),
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });
  const deleteMut = trpc.pricing.deleteAccount.useMutation({
    onSuccess: () => {
      utils.pricing.getAccounts.invalidate();
      toast.success("Conta removida!");
    },
  });
  const createAccMut = trpc.pricing.createAccount.useMutation({
    onSuccess: () => {
      utils.pricing.getAccounts.invalidate();
      toast.success("Conta adicionada!");
      setShowAddAccRow(false);
      setNewAcc({ name: "", platform: "mercadolivre", kitNumber: "1", commission: "11" });
    },
    onError: (err) => toast.error(`Erro ao adicionar: ${err.message}`),
  });

  const autoMatchMut = trpc.pricing.autoMatchIntegrations.useMutation({
    onSuccess: (data) => {
      utils.pricing.getAccounts.invalidate();
      if (data.updated > 0) toast.success(`${data.updated} conta(s) vinculada(s) com sucesso!`);
      else toast.info("Todas as contas já estão vinculadas.");
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const [showAddAccRow, setShowAddAccRow] = useState(false);
  const [newAcc, setNewAcc] = useState({ name: "", platform: "mercadolivre", kitNumber: "1", commission: "11" });
  const [accSearchText, setAccSearchText] = useState("");
  const [accFilterPlatform, setAccFilterPlatform] = useState<string>("all");
  const newAccNameRef = useRef<HTMLInputElement>(null);

  // ── Duplicate account state ──
  const [dupDialogOpen, setDupDialogOpen] = useState(false);
  const [dupSource, setDupSource] = useState<PricingAccount | null>(null);
  const [dupName, setDupName] = useState("");
  const dupNameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (dupDialogOpen && dupNameRef.current) {
      setTimeout(() => dupNameRef.current?.focus(), 100);
    }
  }, [dupDialogOpen]);

  const openDuplicateDialog = (acc: PricingAccount) => {
    setDupSource(acc);
    setDupName(`${acc.name} (cópia)`);
    setDupDialogOpen(true);
  };

  const handleDuplicate = () => {
    if (!dupSource || !dupName.trim()) { toast.error("Nome é obrigatório"); return; }
    createAccMut.mutate({
      name: dupName.trim(),
      platform: dupSource.platform as any,
      listingType: dupSource.listingType || undefined,
      department,
      kitNumber: dupSource.kitNumber,
      commission: dupSource.commission,
      transport: dupSource.transport || undefined,
      margin1: dupSource.margin1 || undefined,
      shipping1: dupSource.shipping1 || undefined,
      margin2: dupSource.margin2 || undefined,
      shipping2: dupSource.shipping2 || undefined,
      margin3: dupSource.margin3 || undefined,
      shipping3: dupSource.shipping3 || undefined,
      margin4: dupSource.margin4 || undefined,
      shipping4: dupSource.shipping4 || undefined,
      margin5: dupSource.margin5 || undefined,
      shipping5: dupSource.shipping5 || undefined,
      integrationId: dupSource.integrationId || undefined,
      sortOrder: (dupSource.sortOrder || 0) + 1,
    }, {
      onSuccess: () => {
        setDupDialogOpen(false);
        setDupSource(null);
        setDupName("");
        toast.success(`Conta "${dupName.trim()}" duplicada com sucesso!`);
      },
    });
  };

  useEffect(() => {
    if (showAddAccRow && newAccNameRef.current) {
      newAccNameRef.current.focus();
    }
  }, [showAddAccRow]);

  const handleAddAccount = () => {
    if (!newAcc.name.trim()) { toast.error("Nome é obrigatório"); return; }
    const commissionDecimal = (parseFloat(newAcc.commission) / 100).toFixed(4);
    createAccMut.mutate({
      name: newAcc.name.trim(),
      platform: newAcc.platform as any,
      department,
      kitNumber: parseInt(newAcc.kitNumber) || 1,
      commission: commissionDecimal,
    });
  };

  const [editingId, setEditingId] = useState<{ id: number; field: string } | null>(null);
  const [editVal, setEditVal] = useState("");
  const [savedFields, setSavedFields] = useState<Set<string>>(new Set());
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId && editRef.current) {
      editRef.current.focus();
      editRef.current.select();
    }
  }, [editingId]);

  const flashField = (id: number, field: string) => {
    const key = `${id}-${field}`;
    setSavedFields(prev => new Set(prev).add(key));
    setTimeout(() => setSavedFields(prev => { const n = new Set(prev); n.delete(key); return n; }), 1500);
  };

  const startEdit = (id: number, field: string, value: string) => {
    setEditingId({ id, field });
    // Para margens e comissão: converter decimal para percentual ao abrir
    if (field.startsWith("margin") || field === "commission") {
      const num = parseFloat(value);
      if (!isNaN(num)) {
        setEditVal((num * 100).toFixed(1));
      } else {
        setEditVal(value);
      }
    } else {
      setEditVal(value);
    }
  };

  const commitAccountEdit = () => {
    if (!editingId) return;
    const { id, field } = editingId;
    const val = editVal.trim();
    // Permitir valor vazio para margem, frete e observação (limpar para tracinho)
    const allowEmpty = field.startsWith("margin") || field.startsWith("shipping") || field.startsWith("observation");
    if (!val && !allowEmpty) { setEditingId(null); return; }

    const update: Record<string, unknown> = {};
    if (field === "name") {
      update.name = val;
    } else if (field === "platform") {
      update.platform = val;
    } else if (field === "listingType") {
      update.listingType = val;
    } else if (field === "commission") {
      const pct = parseFloat(val);
      if (!isNaN(pct)) update.commission = (pct / 100).toFixed(4);
    } else if (field === "kitNumber") {
      update.kitNumber = parseInt(val) || 1;
    } else if (field.startsWith("margin")) {
      // Se vazio ou '-', salvar como "-" (não se aplica)
      if (!val || val === "-" || val === "—") {
        (update as any)[field] = "-";
      } else {
        const pct = parseFloat(val);
        if (!isNaN(pct)) (update as any)[field] = (pct / 100).toFixed(4);
      }
    } else if (field.startsWith("shipping")) {
      // Se vazio ou '-', salvar como null
      if (!val || val === "-" || val === "—") {
        (update as any)[field] = null;
      } else {
        (update as any)[field] = val;
      }
    } else if (field.startsWith("observation")) {
      (update as any)[field] = val || null;
    }
    
    if (Object.keys(update).length > 0) {
      updateMut.mutate({ id, ...update } as any);
      flashField(id, field);
    }
    setEditingId(null);
    setEditVal("");
  };

  const renderEditableCell = (acc: PricingAccount, field: string, displayValue: string, rawValue: string) => {
    const isEd = editingId?.id === acc.id && editingId?.field === field;
    const isSaved = savedFields.has(`${acc.id}-${field}`);
    const isObservation = field.startsWith("observation");
    const alignClass = isObservation ? "text-left" : "text-center";

    return (
      <td
        className={`border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-xs cursor-pointer ${alignClass} ${
          isEd ? "ring-2 ring-blue-600 ring-inset !bg-white dark:!bg-gray-900" :
          isSaved ? "!bg-green-50 dark:!bg-green-900/20" : ""
        }${isObservation ? " min-w-[180px] max-w-[300px]" : ""}`}
        onClick={() => { if (!isEd) startEdit(acc.id, field, rawValue); }}
      >
        {isEd ? (
          <input
            ref={editRef}
            type="text"
            value={editVal}
            onChange={e => setEditVal(e.target.value)}
            onBlur={commitAccountEdit}
            onKeyDown={e => {
              if (e.key === "Enter") { e.preventDefault(); commitAccountEdit(); }
              if (e.key === "Escape") { e.preventDefault(); setEditingId(null); }
            }}
            className={`w-full text-xs border-0 outline-none bg-transparent p-0 ${alignClass}`}
          />
        ) : (
          <span className={`relative ${isObservation && displayValue === "—" ? "text-muted-foreground" : ""}`}>
            {displayValue}
            {isSaved && <Check className="h-2.5 w-2.5 text-green-500 inline ml-1" />}
          </span>
        )}
      </td>
    );
  };

  // Filter and sort accounts: by platform group then alphabetically by name
  const sortedAccounts = useMemo(() => {
    let result = [...accounts];
    if (accFilterPlatform !== "all") {
      result = result.filter(a => a.platform === accFilterPlatform);
    }
    if (accSearchText) {
      const q = accSearchText.toLowerCase();
      result = result.filter(a => (a.name || "").toLowerCase().includes(q) || (a.platform || "").toLowerCase().includes(q));
    }
    // Sort by platform first, then alphabetically by name
    result.sort((a, b) => {
      const platCmp = a.platform.localeCompare(b.platform);
      if (platCmp !== 0) return platCmp;
      return (a.name || "").localeCompare(b.name || "");
    });
    return result;
  }, [accounts, accFilterPlatform, accSearchText]);

  // Group sorted accounts by platform for display
  const groupedAccounts = useMemo(() => {
    const map = new Map<string, PricingAccount[]>();
    for (const acc of sortedAccounts) {
      const key = acc.platform;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(acc);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [sortedAccounts]);

  const platformLabel = (p: string) => p === "mercadolivre" ? "ML" : p === "shopee" ? "Shopee" : p === "amazon" ? "Amazon" : p === "magalu" ? "Magalu" : p === "temu" ? "Temu" : p === "aliexpress" ? "AliExpress" : p === "tiktok" ? "TikTok" : p;

  const typeHeaders = (department === "celular" || department === "catalogo" || department === "eletro")
    ? ["Acessórios", "Reg.Diversos", "Reg.Uranyx", "Robusto", "Apple"]
    : ['8"/Acess.', '12"', '18"/20"', '24"+', "Queima"];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Contas de Venda — {department === "celular" ? "Celular" : department === "mala" ? "Mala" : department === "eletro" ? "Eletro" : "Catálogo ML"}</h3>
          <p className="text-sm text-muted-foreground">
            {accounts.length} conta(s) com 5 pares margem/frete por tipo — clique para editar
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => autoMatchMut.mutate()}
            disabled={autoMatchMut.isPending}
          >
            {autoMatchMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
            Vincular Integrações
          </Button>
          <Button
            size="sm"
            className="gap-1.5"
            onClick={() => setShowAddAccRow(true)}
            disabled={showAddAccRow}
          >
            <Plus className="h-4 w-4" />
            Adicionar Conta
          </Button>
        </div>
      </div>

      {/* Search and platform filter */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            placeholder="Buscar conta..."
            value={accSearchText}
            onChange={e => setAccSearchText(e.target.value)}
            className="w-full pl-8 pr-3 py-1.5 text-sm border rounded-md bg-background"
          />
        </div>
        <select
          value={accFilterPlatform}
          onChange={e => setAccFilterPlatform(e.target.value)}
          className="text-sm border rounded-md px-2 py-1.5 bg-background"
        >
          <option value="all">Todas plataformas</option>
          <option value="shopee">Shopee</option>
          <option value="mercadolivre">ML</option>
          <option value="amazon">Amazon</option>
          <option value="magalu">Magalu</option>
          <option value="tiktok">TikTok</option>
          <option value="aliexpress">AliExpress</option>
          <option value="temu">Temu</option>
        </select>
        <span className="text-xs text-muted-foreground">{sortedAccounts.length} conta(s)</span>
      </div>

      <div className="border rounded-lg overflow-auto max-h-[calc(100vh-380px)]">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-muted z-10">
            <tr>
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 min-w-[100px]">Nome</th>
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Plataforma</th>

              <th className="text-center px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Kit</th>
              <th className="text-center px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Comissão</th>
              {[1, 2, 3, 4, 5].map(t => (
                <th key={t} colSpan={2} className="text-center px-1 py-2 font-medium border-b border-gray-200 dark:border-gray-700">
                  <div className="text-[10px]">{typeHeaders[t - 1]}</div>
                  <div className="text-[9px] text-muted-foreground flex justify-center gap-2">
                    <span>marg</span><span>frete</span>
                  </div>
                </th>
              ))}
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 min-w-[140px]">Obs 1</th>
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 min-w-[140px]">Obs 2</th>
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 min-w-[140px]">Obs 3</th>
              <th className="text-center px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 w-20"></th>
            </tr>
          </thead>
          <tbody>
            {showAddAccRow && (
              <tr className="bg-blue-50/50 dark:bg-blue-900/10">
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <input
                    ref={newAccNameRef}
                    type="text"
                    placeholder="Nome da conta"
                    value={newAcc.name}
                    onChange={e => setNewAcc(p => ({ ...p, name: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter") handleAddAccount(); if (e.key === "Escape") setShowAddAccRow(false); }}
                    className="w-full text-xs border rounded px-1.5 py-1 bg-background"
                  />
                </td>
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <select
                    value={newAcc.platform}
                    onChange={e => setNewAcc(p => ({ ...p, platform: e.target.value }))}
                    className="w-full text-xs border rounded px-1 py-1 bg-background"
                  >
                    <option value="mercadolivre">ML</option>
                    <option value="shopee">Shopee</option>
                    <option value="amazon">Amazon</option>
                    <option value="magalu">Magalu</option>
                    <option value="temu">Temu</option>
                    <option value="aliexpress">AliExpress</option>
                    <option value="tiktok">TikTok</option>
                  </select>
                </td>
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <input
                    type="number"
                    min="1" max="4"
                    value={newAcc.kitNumber}
                    onChange={e => setNewAcc(p => ({ ...p, kitNumber: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter") handleAddAccount(); if (e.key === "Escape") setShowAddAccRow(false); }}
                    className="w-full text-xs border rounded px-1.5 py-1 bg-background text-center"
                  />
                </td>
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <div className="flex items-center">
                    <input
                      type="number"
                      placeholder="11"
                      value={newAcc.commission}
                      onChange={e => setNewAcc(p => ({ ...p, commission: e.target.value }))}
                      onKeyDown={e => { if (e.key === "Enter") handleAddAccount(); if (e.key === "Escape") setShowAddAccRow(false); }}
                      className="w-full text-xs border rounded px-1.5 py-1 bg-background text-center"
                    />
                    <span className="text-xs text-muted-foreground ml-0.5">%</span>
                  </div>
                </td>
                {/* Empty cells for margin/shipping columns */}
                {[1, 2, 3, 4, 5].map(t => (
                  <Fragment key={t}>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1 text-center text-xs text-muted-foreground">—</td>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1 text-center text-xs text-muted-foreground">—</td>
                  </Fragment>
                ))}
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1 text-center text-xs text-muted-foreground">—</td>
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1 text-center">
                  <div className="flex gap-0.5 justify-center">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-green-600 hover:text-green-700"
                      onClick={handleAddAccount}
                      disabled={createAccMut.isPending}
                    >
                      {createAccMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-destructive"
                      onClick={() => setShowAddAccRow(false)}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                </td>
              </tr>
            )}
            {groupedAccounts.map(([platform, accs]) => (
              <Fragment key={platform}>
                <tr className="bg-muted/60">
                  <td colSpan={16} className="px-2 py-1.5 text-xs font-bold uppercase text-muted-foreground border-b border-gray-200 dark:border-gray-700">
                    {platformLabel(platform)} &nbsp;&middot;&nbsp; {accs.length} conta(s)
                  </td>
                </tr>
                {accs.map(acc => {
              const commissionPct = Math.round((parseFloat(acc.commission) || 0) * 100);
              const platLabel = acc.platform === "mercadolivre" ? "ML" : acc.platform === "shopee" ? "Shopee" : acc.platform === "amazon" ? "Amazon" : acc.platform === "magalu" ? "Magalu" : acc.platform === "temu" ? "Temu" : acc.platform === "aliexpress" ? "AliExpress" : acc.platform === "tiktok" ? "TikTok" : acc.platform;
              const typeLabel = acc.listingType || acc.platform;

              return (
                <tr key={acc.id} className={`hover:bg-accent/30 ${!acc.isActive ? "opacity-50" : ""}`}>
                  {renderEditableCell(acc, "name", acc.name, acc.name)}
                  {renderEditableCell(acc, "platform", platLabel, acc.platform)}

                  {renderEditableCell(acc, "kitNumber", acc.kitNumber.toString(), acc.kitNumber.toString())}
                  {renderEditableCell(acc, "commission", `${commissionPct}%`, (parseFloat(acc.commission) || 0).toString())}
                  {[1, 2, 3, 4, 5].map(t => {
                    const mKey = `margin${t}` as keyof PricingAccount;
                    const sKey = `shipping${t}` as keyof PricingAccount;
                    const mVal = acc[mKey] as string | null;
                    const sVal = acc[sKey] as string | null;
                    const mDisplay = mVal && mVal !== "-" ? (parseFloat(mVal) * 100).toFixed(1) + "%" : "—";
                    const sDisplay = sVal ? parseFloat(sVal).toFixed(0) : "—";
                    const mRaw = mVal && mVal !== "-" ? mVal : "";
                    const sRaw = sVal ? sVal : "";

                    return (
                      <Fragment key={t}>
                        {renderEditableCell(acc, `margin${t}`, mDisplay, mRaw)}
                        {renderEditableCell(acc, `shipping${t}`, sDisplay, sRaw)}
                      </Fragment>
                    );
                  })}
                  {/* Observações */}
                  {renderEditableCell(acc, "observation", acc.observation || "—", acc.observation || "")}
                  {renderEditableCell(acc, "observation2", acc.observation2 || "—", acc.observation2 || "")}
                  {renderEditableCell(acc, "observation3", acc.observation3 || "—", acc.observation3 || "")}
                  <td className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center">
                    <div className="flex gap-0.5 justify-center">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-blue-600 hover:text-blue-700"
                        onClick={() => openDuplicateDialog(acc)}
                        title={`Duplicar conta "${acc.name}"`}
                      >
                        <Copy className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-destructive hover:text-destructive"
                        onClick={() => {
                          if (confirm(`Remover conta "${acc.name}"?`)) {
                            deleteMut.mutate({ id: acc.id });
                          }
                        }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/* Duplicate Account Dialog */}
      <Dialog open={dupDialogOpen} onOpenChange={setDupDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Duplicar Conta</DialogTitle>
          </DialogHeader>
          {dupSource && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Criando cópia de <strong>{dupSource.name}</strong> ({dupSource.platform}) com todas as margens, fretes e comissão.
              </p>
              <div>
                <label className="text-sm font-medium">Nome da nova conta</label>
                <Input
                  ref={dupNameRef}
                  value={dupName}
                  onChange={e => setDupName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") handleDuplicate(); }}
                  placeholder="Nome da conta"
                  className="mt-1"
                />
              </div>
              <div className="text-xs text-muted-foreground space-y-1 bg-muted/50 rounded p-3">
                <p><strong>Será copiado:</strong></p>
                <p>Plataforma: {dupSource.platform} · Kit: {dupSource.kitNumber} · Comissão: {Math.round((parseFloat(dupSource.commission) || 0) * 100)}%</p>
                <p>Transporte: {dupSource.transport || "—"} · Integração: {dupSource.integrationId ? "Sim" : "Não"}</p>
                <p>5 pares de margem/frete copiados integralmente</p>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDupDialogOpen(false)}>Cancelar</Button>
            <Button onClick={handleDuplicate} disabled={createAccMut.isPending} className="gap-1.5">
              {createAccMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Copy className="h-4 w-4" />}
              Duplicar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════
// ── PRODUCTS TABLE ──
// ══════════════════════════════════════════════════════════════════

function ProductsTable({ department, products }: { department: Department; products: PricingProduct[] }) {
  const utils = trpc.useUtils();
  const skuAuditQuery = trpc.pricing.getSkuAudit.useQuery(undefined, { staleTime: 5 * 60 * 1000 });
  const [showAuditList, setShowAuditList] = useState(false);

  // Fetch all products to determine which SKUs are in catalogo
  const allProductsQuery = trpc.pricing.getProducts.useQuery();
  const catalogSkus = useMemo(() => {
    const all = (allProductsQuery.data || []) as PricingProduct[];
    return new Set(all.filter(p => p.department === "catalogo").map(p => p.sku));
  }, [allProductsQuery.data]);

  const toggleCatalogMut = trpc.pricing.toggleCatalog.useMutation({
    onSuccess: (data, variables) => {
      utils.pricing.getProducts.invalidate();
      toast.success(variables.enabled ? "Produto adicionado ao Catálogo ML" : "Produto removido do Catálogo ML");
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const updateMut = trpc.pricing.updateProduct.useMutation({
    onSuccess: () => utils.pricing.getProducts.invalidate(),
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });
  const deleteMut = trpc.pricing.deleteProduct.useMutation({
    onSuccess: () => {
      utils.pricing.getProducts.invalidate();
      toast.success("Produto removido!");
    },
  });
  const createMut = trpc.pricing.createProduct.useMutation({
    onSuccess: () => {
      utils.pricing.getProducts.invalidate();
      toast.success("Produto adicionado!");
      setShowAddRow(false);
      setNewProduct({ sku: "", name: "", costKit1: "", costKit2: "", costKit3: "", costKit4: "", productType: 3, description: "", model: "" });
    },
    onError: (err) => toast.error(`Erro ao adicionar: ${err.message}`),
  });

  const [showAddRow, setShowAddRow] = useState(false);
  const [newProduct, setNewProduct] = useState({ sku: "", name: "", costKit1: "", costKit2: "", costKit3: "", costKit4: "", productType: 3, description: "", model: "" });
  const newSkuRef = useRef<HTMLInputElement>(null);

  const [editingId, setEditingId] = useState<{ id: number; field: string } | null>(null);
  const [editVal, setEditVal] = useState("");
  const [savedFields, setSavedFields] = useState<Set<string>>(new Set());
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId && editRef.current) {
      editRef.current.focus();
      editRef.current.select();
    }
  }, [editingId]);

  const flashField = (id: number, field: string) => {
    const key = `${id}-${field}`;
    setSavedFields(prev => new Set(prev).add(key));
    setTimeout(() => setSavedFields(prev => { const n = new Set(prev); n.delete(key); return n; }), 1500);
  };

  const startEdit = (id: number, field: string, value: string) => {
    setEditingId({ id, field });
    setEditVal(value);
  };

  const commitProductEdit = () => {
    if (!editingId) return;
    const { id, field } = editingId;
    const val = editVal.trim();
    if (!val) { setEditingId(null); return; }

    if (field === "sku") updateMut.mutate({ id, sku: val });
    else if (field === "name") updateMut.mutate({ id, name: val });
    else if (field === "costKit1") updateMut.mutate({ id, costKit1: parseFloat(val).toFixed(2) });
    else if (field === "costKit2") updateMut.mutate({ id, costKit2: parseFloat(val).toFixed(2) });
    else if (field === "costKit3") updateMut.mutate({ id, costKit3: parseFloat(val).toFixed(2) });
    else if (field === "costKit4") updateMut.mutate({ id, costKit4: parseFloat(val).toFixed(2) });
    else if (field === "productType") updateMut.mutate({ id, productType: parseInt(val) || 2 });
    else if (field === "ean") updateMut.mutate({ id, ean: val });
    else return;
    flashField(id, field);
    setEditingId(null);
    setEditVal("");
  };

  const renderEditableCell = (prod: PricingProduct, field: string, displayValue: string, rawValue: string, align = "text-right") => {
    const isEd = editingId?.id === prod.id && editingId?.field === field;
    const isSaved = savedFields.has(`${prod.id}-${field}`);

    return (
      <td
        className={`border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-xs cursor-pointer ${align} ${
          isEd ? "ring-2 ring-blue-600 ring-inset !bg-white dark:!bg-gray-900" :
          isSaved ? "!bg-green-50 dark:!bg-green-900/20" : ""
        }`}
        onClick={() => { if (!isEd) startEdit(prod.id, field, rawValue); }}
      >
        {isEd ? (
          <input
            ref={editRef}
            type="text"
            value={editVal}
            onChange={e => setEditVal(e.target.value)}
            onBlur={commitProductEdit}
            onKeyDown={e => {
              if (e.key === "Enter") { e.preventDefault(); commitProductEdit(); }
              if (e.key === "Escape") { e.preventDefault(); setEditingId(null); }
            }}
            className={`w-full text-xs border-0 outline-none bg-transparent p-0 ${align}`}
          />
        ) : (
          <span className="relative">
            {displayValue}
            {isSaved && <Check className="h-2.5 w-2.5 text-green-500 inline ml-1" />}
          </span>
        )}
      </td>
    );
  };

  useEffect(() => {
    if (showAddRow && newSkuRef.current) {
      newSkuRef.current.focus();
    }
  }, [showAddRow]);

  const handleAddProduct = () => {
    if (!newProduct.sku.trim() || !newProduct.name.trim() || !newProduct.costKit1.trim()) {
      toast.error("Preencha pelo menos SKU, Nome e Custo Kit 1");
      return;
    }
    createMut.mutate({
      sku: newProduct.sku.trim(),
      name: newProduct.name.trim(),
      department,
      productType: newProduct.productType,
      costKit1: parseFloat(newProduct.costKit1).toFixed(2),
      costKit2: newProduct.costKit2 ? parseFloat(newProduct.costKit2).toFixed(2) : null,
      costKit3: newProduct.costKit3 ? parseFloat(newProduct.costKit3).toFixed(2) : null,
      costKit4: newProduct.costKit4 ? parseFloat(newProduct.costKit4).toFixed(2) : null,
      description: newProduct.description || undefined,
      model: newProduct.model || undefined,
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Produtos — {department === "celular" ? "Celular" : department === "mala" ? "Mala" : department === "eletro" ? "Eletro" : "Catálogo ML"}</h3>
          <p className="text-sm text-muted-foreground">{products.length} produto(s) — clique para editar</p>
        </div>
        <Button
          size="sm"
          className="gap-1.5"
          onClick={() => setShowAddRow(true)}
          disabled={showAddRow}
        >
          <Plus className="h-4 w-4" />
          Adicionar Produto
        </Button>
      </div>

      {/* SKU Audit Banner */}
      {skuAuditQuery.data && skuAuditQuery.data.length > 0 && (() => {
        const activeItems = skuAuditQuery.data.filter((i: any) => !i.dismissed);
        const dismissedItems = skuAuditQuery.data.filter((i: any) => i.dismissed);
        if (activeItems.length === 0 && !showAuditList) return null;
        return (
        <div className="border border-amber-300 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-700 rounded-lg px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0" />
              <span className="text-sm font-medium text-amber-800 dark:text-amber-300">
                {activeItems.length} produto(s) do Bling com pendências
                {dismissedItems.length > 0 && <span className="text-xs opacity-70 ml-1">({dismissedItems.length} dispensado(s))</span>}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs text-amber-700 dark:text-amber-400"
                disabled={skuAuditQuery.isFetching}
                onClick={() => {
                  utils.pricing.getSkuAudit.invalidate();
                }}
              >
                <RefreshCw className={`h-3 w-3 mr-1 ${skuAuditQuery.isFetching ? "animate-spin" : ""}`} />
                Atualizar
              </Button>
              <Button variant="ghost" size="sm" className="h-7 text-xs text-amber-700 dark:text-amber-400" onClick={() => setShowAuditList(!showAuditList)}>
                {showAuditList ? "Ocultar" : "Ver SKUs"}
                <ChevronDown className={`h-3 w-3 ml-1 transition-transform ${showAuditList ? "rotate-180" : ""}`} />
              </Button>
            </div>
          </div>
          {showAuditList && (
            <div className="mt-2 max-h-96 overflow-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="sticky top-0 bg-amber-100 dark:bg-amber-900/40">
                  <tr>
                    <th className="text-left px-2 py-1 font-medium">SKU</th>
                    <th className="text-left px-2 py-1 font-medium">Produto</th>
                    <th className="text-center px-2 py-1 font-medium w-16">Estoque</th>
                    <th className="text-left px-2 py-1 font-medium">Contas</th>
                    <th className="text-left px-2 py-1 font-medium">Pendências</th>
                    <th className="text-center px-2 py-1 font-medium w-16">Ação</th>
                  </tr>
                </thead>
                <tbody>
                  {activeItems.map((item: any) => (
                    <AuditRow key={item.id} item={item} isDismissed={false} />
                  ))}
                  {dismissedItems.length > 0 && (
                    <tr><td colSpan={6} className="px-2 py-1.5 text-[10px] font-semibold text-amber-600 dark:text-amber-400 bg-amber-100/50 dark:bg-amber-800/20">
                      Dispensados ({dismissedItems.length})
                    </td></tr>
                  )}
                  {dismissedItems.map((item: any) => (
                    <AuditRow key={item.id} item={item} isDismissed={true} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        );
      })()}

      <div className="border rounded-lg overflow-auto max-h-[calc(100vh-320px)]">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-muted z-10">
            <tr>
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700" style={{ maxWidth: '120px' }}>SKU</th>
              <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Produto</th>
              <th className="text-left px-1 py-2 font-medium border-b border-gray-200 dark:border-gray-700" style={{ width: '50px', maxWidth: '50px' }}>EAN</th>
              {department === "mala" && (
                <>
                  <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Descrição</th>
                  <th className="text-left px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Modelo</th>
                </>
              )}
              {department === "celular" ? (
                <>
                  <th className="text-right px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Kit 1</th>
                  <th className="text-right px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Kit 2</th>
                  <th className="text-right px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Kit 3</th>
                  <th className="text-right px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Kit 4</th>
                </>
              ) : (
                <th className="text-right px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Custo</th>
              )}
              <th className="text-center px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700">Tabela</th>
              {department !== "catalogo" && (
                <th className="text-center px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 w-16">Catálogo</th>
              )}
              <th className="text-center px-2 py-2 font-medium border-b border-gray-200 dark:border-gray-700 w-10"></th>
            </tr>
          </thead>
          <tbody>
            {showAddRow && (
              <tr className="bg-blue-50/50 dark:bg-blue-900/10">
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <input
                    ref={newSkuRef}
                    type="text"
                    placeholder="SKU"
                    value={newProduct.sku}
                    onChange={e => setNewProduct(p => ({ ...p, sku: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                    className="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                  />
                </td>
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <input
                    type="text"
                    placeholder="Nome do produto"
                    value={newProduct.name}
                    onChange={e => setNewProduct(p => ({ ...p, name: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                    className="w-full text-xs border rounded px-1.5 py-1 bg-background"
                  />
                </td>
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                  <input
                    type="text"
                    placeholder="EAN"
                    value={(newProduct as any).ean || ""}
                    onChange={e => setNewProduct(p => ({ ...p, ean: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                    className="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                  />
                </td>
                {department === "mala" && (
                  <>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                      <input
                        type="text"
                        placeholder="Descrição"
                        value={newProduct.description}
                        onChange={e => setNewProduct(p => ({ ...p, description: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                        className="w-full text-xs border rounded px-1.5 py-1 bg-background"
                      />
                    </td>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                      <input
                        type="text"
                        placeholder="Modelo"
                        value={newProduct.model}
                        onChange={e => setNewProduct(p => ({ ...p, model: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                        className="w-full text-xs border rounded px-1.5 py-1 bg-background"
                      />
                    </td>
                  </>
                )}
                {department === "celular" ? (
                  <>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                      <input
                        type="number"
                        placeholder="Kit 1"
                        value={newProduct.costKit1}
                        onChange={e => setNewProduct(p => ({ ...p, costKit1: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                        className="w-full text-xs border rounded px-1.5 py-1 bg-background text-right"
                      />
                    </td>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                      <input
                        type="number"
                        placeholder="Kit 2"
                        value={newProduct.costKit2}
                        onChange={e => setNewProduct(p => ({ ...p, costKit2: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                        className="w-full text-xs border rounded px-1.5 py-1 bg-background text-right"
                      />
                    </td>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                      <input
                        type="number"
                        placeholder="Kit 3"
                        value={newProduct.costKit3}
                        onChange={e => setNewProduct(p => ({ ...p, costKit3: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                        className="w-full text-xs border rounded px-1.5 py-1 bg-background text-right"
                      />
                    </td>
                    <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                      <input
                        type="number"
                        placeholder="Kit 4"
                        value={newProduct.costKit4}
                        onChange={e => setNewProduct(p => ({ ...p, costKit4: e.target.value }))}
                        onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                        className="w-full text-xs border rounded px-1.5 py-1 bg-background text-right"
                      />
                    </td>
                  </>
                ) : (
                  <td className="border border-gray-200 dark:border-gray-700 px-1 py-1">
                    <input
                      type="number"
                      placeholder="Custo"
                      value={newProduct.costKit1}
                      onChange={e => setNewProduct(p => ({ ...p, costKit1: e.target.value }))}
                      onKeyDown={e => { if (e.key === "Enter") handleAddProduct(); if (e.key === "Escape") setShowAddRow(false); }}
                      className="w-full text-xs border rounded px-1.5 py-1 bg-background text-right"
                    />
                  </td>
                )}
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1 text-center">
                  <Select
                    value={newProduct.productType.toString()}
                    onValueChange={(v: string) => setNewProduct(p => ({ ...p, productType: parseInt(v) }))}
                  >
                    <SelectTrigger className="h-7 text-[10px] border px-1">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${TYPE_COLORS[newProduct.productType] || ""}`}>
                        {getTypeLabel(department, newProduct.productType)}
                      </span>
                    </SelectTrigger>
                    <SelectContent>
                      {[1, 2, 3, 4, 5].map(t => (
                        <SelectItem key={t} value={t.toString()}>
                          {getTypeLabel(department, t)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                {department !== "catalogo" && (
                  <td className="border border-gray-200 dark:border-gray-700 px-1 py-1"></td>
                )}
                <td className="border border-gray-200 dark:border-gray-700 px-1 py-1 text-center">
                  <div className="flex gap-0.5 justify-center">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-green-600"
                      onClick={handleAddProduct}
                      disabled={createMut.isPending}
                    >
                      {createMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-destructive"
                      onClick={() => setShowAddRow(false)}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                </td>
              </tr>
            )}
            {products.map(p => (
              <tr key={p.id} className="hover:bg-accent/30">
                <td
                  className={`border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-xs text-left font-mono cursor-pointer ${
                    editingId?.id === p.id && editingId?.field === "sku" ? "ring-2 ring-blue-600 ring-inset !bg-white dark:!bg-gray-900" :
                    savedFields.has(`${p.id}-sku`) ? "!bg-green-50 dark:!bg-green-900/20" : ""
                  }`}
                  style={{ maxWidth: '120px' }}
                  title={p.sku || ""}
                  onClick={() => { if (!(editingId?.id === p.id && editingId?.field === "sku")) startEdit(p.id, "sku", p.sku || ""); }}
                >
                  {editingId?.id === p.id && editingId?.field === "sku" ? (
                    <input
                      ref={editRef}
                      type="text"
                      value={editVal}
                      onChange={e => setEditVal(e.target.value)}
                      onBlur={commitProductEdit}
                      onKeyDown={e => {
                        if (e.key === "Enter") { e.preventDefault(); commitProductEdit(); }
                        if (e.key === "Escape") { e.preventDefault(); setEditingId(null); }
                      }}
                      className="w-full text-xs border-0 outline-none bg-transparent p-0 text-left font-mono"
                    />
                  ) : (
                    <span className="block truncate">
                      {p.sku || "—"}
                      {savedFields.has(`${p.id}-sku`) && <Check className="h-2.5 w-2.5 text-green-500 inline ml-1" />}
                    </span>
                  )}
                </td>
                {renderEditableCell(p, "name", p.name, p.name, "text-left")}
                {renderEditableCell(p, "ean", p.ean || '—', p.ean || '', "text-left")}
                {department === "mala" && (
                  <>
                    <td className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-xs text-left">
                      {p.description || "—"}
                    </td>
                    <td className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-xs text-left">
                      {p.model || "—"}
                    </td>
                  </>
                )}
                {department === "celular" ? (
                  <>
                    {renderEditableCell(p, "costKit1", `R$ ${(parseFloat(p.costKit1) || 0).toFixed(0)}`, (parseFloat(p.costKit1) || 0).toFixed(0))}
                    {renderEditableCell(p, "costKit2", `R$ ${(parseFloat(p.costKit2 || "0") || 0).toFixed(0)}`, (parseFloat(p.costKit2 || "0") || 0).toFixed(0))}
                    {renderEditableCell(p, "costKit3", `R$ ${(parseFloat(p.costKit3 || "0") || 0).toFixed(0)}`, (parseFloat(p.costKit3 || "0") || 0).toFixed(0))}
                    {renderEditableCell(p, "costKit4", `R$ ${(parseFloat(p.costKit4 || "0") || 0).toFixed(0)}`, (parseFloat(p.costKit4 || "0") || 0).toFixed(0))}
                  </>
                ) : (
                  renderEditableCell(p, "costKit1", `R$ ${(parseFloat(p.costKit1) || 0).toFixed(0)}`, (parseFloat(p.costKit1) || 0).toFixed(0))
                )}
                <td className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center">
                  <Select
                    value={p.productType.toString()}
                    onValueChange={(v: string) => {
                      updateMut.mutate({ id: p.id, productType: parseInt(v) });
                      flashField(p.id, "productType");
                    }}
                  >
                    <SelectTrigger className="h-6 text-[10px] border-0 bg-transparent px-1">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${TYPE_COLORS[p.productType] || ""}`}>
                        {getTypeLabel(department, p.productType)}
                      </span>
                    </SelectTrigger>
                    <SelectContent>
                      {[1, 2, 3, 4, 5].map(t => (
                        <SelectItem key={t} value={t.toString()}>
                          {getTypeLabel(department, t)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                {department !== "catalogo" && (
                  <td className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center">
                    <input
                      type="checkbox"
                      checked={catalogSkus.has(p.sku)}
                      onChange={(e) => {
                        toggleCatalogMut.mutate({ productId: p.id, enabled: e.target.checked });
                      }}
                      disabled={toggleCatalogMut.isPending}
                      className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
                      title={catalogSkus.has(p.sku) ? "Remover do Catálogo ML" : "Adicionar ao Catálogo ML"}
                    />
                  </td>
                )}
                <td className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-destructive"
                    onClick={() => {
                      if (confirm(`Remover "${p.name}"?`)) deleteMut.mutate({ id: p.id });
                    }}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════
// ── STORE INFO TABLE (Loja) ──
// ══════════════════════════════════════════════════════════════════

const STORE_PLATFORMS = ["aliexpress", "amazon", "magalu", "ml", "shopee", "temu", "tiktok", "shein"] as const;

function getPlatformColor(p: string) {
  const colors: Record<string, string> = {
    aliexpress: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
    amazon: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
    magalu: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
    ml: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
    shopee: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
    temu: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
    tiktok: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200",
    shein: "bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300",
  };
  return colors[p.toLowerCase()] || "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200";
}

type IntegrationItem = { id: number; platform: string; name: string; status: string; isActive: boolean };

function StoreInfoTable({ stores, isLoading, pricingAccounts, integrations }: { stores: StoreInfoItem[]; isLoading: boolean; pricingAccounts: PricingAccount[]; integrations: IntegrationItem[] }) {
  const utils = trpc.useUtils();
  const [editingCell, setEditingCell] = useState<{ id: number; field: string } | null>(null);
  const [editValue, setEditValue] = useState("");
  const [showPasswords, setShowPasswords] = useState<Set<number>>(new Set());
  const [filterPlatform, setFilterPlatform] = useState<string>("all");
  const [searchText, setSearchText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const createMut = trpc.storeInfo.create.useMutation({
    onSuccess: () => { utils.storeInfo.list.invalidate(); toast.success("Loja adicionada"); },
    onError: (err) => toast.error(err.message),
  });
  const updateMut = trpc.storeInfo.update.useMutation({
    onSuccess: () => utils.storeInfo.list.invalidate(),
    onError: (err) => toast.error(err.message),
  });
  const deleteMut = trpc.storeInfo.delete.useMutation({
    onSuccess: () => { utils.storeInfo.list.invalidate(); toast.success("Loja removida"); },
    onError: (err) => toast.error(err.message),
  });
  const setDeptMut = trpc.storeInfo.setDepartment.useMutation({
    onSuccess: () => { utils.storeInfo.list.invalidate(); utils.pricing.getAccounts.invalidate(); toast.success("Departamento atualizado"); },
    onError: (err) => toast.error("Erro: " + err.message),
  });
  const [deptDropdownId, setDeptDropdownId] = useState<number | null>(null);

  const filteredStores = useMemo(() => {
    let result = stores;
    if (filterPlatform !== "all") result = result.filter(s => s.platform.toLowerCase() === filterPlatform);
    if (searchText) {
      const q = searchText.toLowerCase();
      result = result.filter(s =>
        (s.accountName || "").toLowerCase().includes(q) ||
        (s.cpfName || "").toLowerCase().includes(q) ||
        (s.email || "").toLowerCase().includes(q) ||
        (s.cnpj || "").toLowerCase().includes(q) ||
        (s.platform || "").toLowerCase().includes(q)
      );
    }
    return result;
  }, [stores, filterPlatform, searchText]);

  // Group by platform, sorted alphabetically within each group
  const grouped = useMemo(() => {
    const map = new Map<string, StoreInfoItem[]>();
    for (const s of filteredStores) {
      const key = s.platform.toLowerCase();
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(s);
    }
    // Sort each group alphabetically by accountName
    map.forEach((items) => {
      items.sort((a: StoreInfoItem, b: StoreInfoItem) => (a.accountName || "").localeCompare(b.accountName || ""));
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [filteredStores]);

  // Stores without pricing accounts
  const storesWithoutPricing = useMemo(() => {
    const pricingNames = new Set(pricingAccounts.map(a => a.name.toLowerCase().trim()));
    return stores.filter(s => {
      const storeName = (s.accountName || "").toLowerCase().trim();
      if (!storeName) return true;
      return !pricingNames.has(storeName);
    });
  }, [stores, pricingAccounts]);
  const [showMissing, setShowMissing] = useState(false);

  // Map store platform to pricing platform
  const STORE_TO_PRICING_PLATFORM: Record<string, string> = {
    shopee: "shopee",
    ml: "mercadolivre",
    amazon: "amazon",
    temu: "temu",
    tiktok: "tiktok",
    aliexpress: "aliexpress",
    magalu: "magalu",
    shein: "shein",
  };

  // Find pricing accounts matching a store (exact name OR name starts with store name)
  const findMatchingAccounts = useCallback((store: StoreInfoItem): PricingAccount[] => {
    const name = (store.accountName || "").toLowerCase().trim();
    if (!name) return [];
    const pricingPlatform = STORE_TO_PRICING_PLATFORM[store.platform.toLowerCase()];
    if (!pricingPlatform) return [];
    return pricingAccounts.filter(a => {
      if (a.platform !== pricingPlatform) return false;
      const acctName = a.name.toLowerCase().trim();
      // Match exact name OR account name starts with store name (e.g. "barbosa classico" starts with "barbosa")
      return acctName === name || acctName.startsWith(name + " ");
    });
  }, [pricingAccounts]);

  const hasPricing = useCallback((store: StoreInfoItem) => {
    // First check by storeInfoId link
    if (pricingAccounts.some(a => a.storeInfoId === store.id)) return true;
    // Fallback: check by name matching
    return findMatchingAccounts(store).length > 0;
  }, [pricingAccounts, findMatchingAccounts]);

  // Get departments for a store based on storeInfoId link in pricing accounts
  const getStoreDepartments = useCallback((store: StoreInfoItem): string[] => {
    return pricingAccounts
      .filter(a => a.storeInfoId === store.id)
      .map(a => a.department);
  }, [pricingAccounts]);

  const getDepartments = useCallback((store: StoreInfoItem): string => {
    const deps = getStoreDepartments(store);
    if (deps.length === 0) {
      // Fallback: check by name matching (for legacy accounts without storeInfoId)
      const matched = findMatchingAccounts(store);
      if (matched.length === 0) return "—";
      const depsSet = new Set(matched.map(a => a.department));
      const parts: string[] = [];
      if (depsSet.has("celular")) parts.push("Cel");
      if (depsSet.has("mala")) parts.push("Mala");
      if (depsSet.has("eletro")) parts.push("Eletro");
      return parts.length > 0 ? parts.join(" + ") : "—";
    }
    const depsSet = new Set(deps);
    const parts: string[] = [];
    if (depsSet.has("celular")) parts.push("Cel");
    if (depsSet.has("mala")) parts.push("Mala");
    if (depsSet.has("eletro")) parts.push("Eletro");
    return parts.length > 0 ? parts.join(" + ") : "—";
  }, [pricingAccounts, findMatchingAccounts, getStoreDepartments]);

  // Map store platform to integration platform
  const PLATFORM_MAP: Record<string, string> = {
    shopee: "shopee",
    ml: "mercadolivre",
    amazon: "amazon",
    temu: "temu",
    tiktok: "tiktok",
  };

  const hasIntegration = useCallback((store: StoreInfoItem) => {
    const storeName = (store.accountName || "").toLowerCase().trim();
    if (!storeName) return false;
    const storePlatform = store.platform.toLowerCase();
    const integPlatform = PLATFORM_MAP[storePlatform];
    if (!integPlatform) return false; // aliexpress, shein etc. have no integrations
    // Normalize: remove spaces for fuzzy matching (e.g. "kfa 2" vs "kfa2")
    const storeNorm = storeName.replace(/\s+/g, "");
    return integrations.some(i => {
      if (i.platform !== integPlatform) return false;
      const integName = i.name.toLowerCase().trim();
      const integNorm = integName.replace(/\s+/g, "");
      // Check if store name matches integration name (handles prefixes like "ML ", "Shoppe ", "Amazon ")
      return integNorm.includes(storeNorm) || storeNorm.includes(integNorm);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [integrations]);

  const startEdit = (id: number, field: string, currentValue: string | null) => {
    setEditingCell({ id, field });
    setEditValue(currentValue || "");
    setTimeout(() => inputRef.current?.focus(), 10);
  };

  const commitEdit = () => {
    if (!editingCell) return;
    const { id, field } = editingCell;
    const val = editValue.trim() || null;
    updateMut.mutate({ id, [field]: val } as any);
    setEditingCell(null);
  };

  const cancelEdit = () => setEditingCell(null);

  const togglePassword = (id: number) => {
    setShowPasswords(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copiado!");
  };

  const renderCell = (store: StoreInfoItem, field: keyof StoreInfoItem, value: string | null, options?: { isPassword?: boolean; isLink?: boolean }) => {
    const isEditing = editingCell?.id === store.id && editingCell?.field === field;
    
    if (isEditing) {
      return (
        <input
          ref={inputRef}
          className="w-full bg-transparent border-b border-primary outline-none text-xs px-1 py-0.5"
          value={editValue}
          onChange={e => setEditValue(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter") commitEdit();
            if (e.key === "Escape") cancelEdit();
          }}
          onBlur={commitEdit}
        />
      );
    }

    if (options?.isPassword && value) {
      const visible = showPasswords.has(store.id);
      return (
        <div className="flex items-center gap-1 group/pwd">
          <span
            className="cursor-pointer text-xs truncate flex-1"
            onClick={() => startEdit(store.id, field as string, value)}
          >
            {visible ? value : "••••••••"}
          </span>
          <button
            className="opacity-0 group-hover/pwd:opacity-100 transition-opacity"
            onClick={() => togglePassword(store.id)}
          >
            {visible ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
          </button>
          {visible && (
            <button
              className="opacity-0 group-hover/pwd:opacity-100 transition-opacity"
              onClick={() => copyToClipboard(value)}
            >
              <Copy className="h-3 w-3" />
            </button>
          )}
        </div>
      );
    }

    if (options?.isLink && value) {
      return (
        <div className="flex items-center gap-1">
          <span
            className="cursor-pointer text-xs truncate flex-1 text-blue-600 dark:text-blue-400 hover:underline"
            onClick={() => startEdit(store.id, field as string, value)}
          >
            {value}
          </span>
          <a href={value.startsWith("http") ? value : `https://${value}`} target="_blank" rel="noopener noreferrer" className="shrink-0">
            <ExternalLink className="h-3 w-3 text-muted-foreground" />
          </a>
        </div>
      );
    }

    return (
      <span
        className="cursor-pointer text-xs block truncate min-h-[18px] hover:bg-muted/50 rounded px-1 py-0.5"
        onClick={() => startEdit(store.id, field as string, value)}
        title={value || ""}
      >
        {value || <span className="text-muted-foreground">—</span>}
      </span>
    );
  };

  const columns = [
    { key: "accountName" as const, label: "Conta", w: "min-w-[65px]" },
    { key: "freight" as const, label: "Frete", w: "min-w-[65px]" },
    { key: "cpfName" as const, label: "Responsável", w: "min-w-[75px]" },
    { key: "server" as const, label: "Servidor", w: "min-w-[40px]" },
    { key: "cnpj" as const, label: "CNPJ", w: "min-w-[95px]" },
    { key: "email" as const, label: "Email", w: "min-w-[90px]" },
    { key: "phone" as const, label: "Fone", w: "min-w-[75px]" },
    { key: "password" as const, label: "Senha", w: "min-w-[65px]" },
    { key: "shippingAddress" as const, label: "End. Envio", w: "", style: { width: 42, maxWidth: 42, overflow: 'hidden' } as React.CSSProperties },
    { key: "returnAddress" as const, label: "End. Dev.", w: "", style: { width: 42, maxWidth: 42, overflow: 'hidden' } as React.CSSProperties },
    { key: "observation" as const, label: "Obs", w: "max-w-[150px]", style: { width: 150, maxWidth: 150, overflow: 'hidden' } as React.CSSProperties },
  ];

  const statusColumns = [
    { key: "tipo" as const, label: "Tipo", w: "min-w-[60px]" },
    { key: "tabPreco" as const, label: "Tab. Preço", w: "min-w-[60px]" },
    { key: "integracao" as const, label: "Integração", w: "min-w-[60px]" },
  ];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Dados das Lojas</h3>
          <p className="text-sm text-muted-foreground">{stores.length} loja(s) — clique para editar qualquer campo</p>
        </div>
        <div className="flex gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              placeholder="Buscar..."
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              className="pl-7 h-8 w-48 text-xs"
            />
          </div>
          <Select value={filterPlatform} onValueChange={setFilterPlatform}>
            <SelectTrigger className="h-8 w-36 text-xs">
              <SelectValue placeholder="Plataforma" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas</SelectItem>
              {STORE_PLATFORMS.map(p => (
                <SelectItem key={p} value={p}>{p}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" className="gap-1.5 h-8">
                <Plus className="h-3.5 w-3.5" />
                Adicionar
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuLabel>Plataforma</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {STORE_PLATFORMS.map(p => (
                <DropdownMenuItem key={p} onClick={() => createMut.mutate({ platform: p, accountName: "Nova conta" })}>
                  {p}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="overflow-x-auto border rounded-lg">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/50">
              <th className="px-2 py-2 text-left font-medium border-b border-r sticky left-0 bg-muted/50 z-10 min-w-[90px]">Plataforma</th>
              {columns.map(c => (
                <th key={c.key} className={`px-2 py-2 text-left font-medium border-b border-r ${c.w}`} style={c.style}>{c.label}</th>
              ))}
              {statusColumns.map(sc => (
                <th key={sc.key} className={`px-2 py-2 text-center font-medium border-b border-r ${sc.w}`}>{sc.label}</th>
              ))}
              <th className="px-2 py-2 text-center font-medium border-b min-w-[40px]"></th>
            </tr>
          </thead>
          <tbody>
            {grouped.map(([platform, items]) => (
              <Fragment key={platform}>
                <tr className="bg-muted/30">
                  <td colSpan={columns.length + statusColumns.length + 2} className="px-2 py-1.5 font-semibold text-xs uppercase tracking-wider">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold ${getPlatformColor(platform)}`}>
                      {platform}
                    </span>
                    <span className="ml-2 text-muted-foreground font-normal normal-case">
                      {items.length} conta(s)
                    </span>
                  </td>
                </tr>
                {items.map(store => (
                  <tr key={store.id} className="hover:bg-muted/20 border-b border-gray-100 dark:border-gray-800">
                    <td className="px-2 py-1.5 border-r sticky left-0 bg-background z-10">
                      <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${getPlatformColor(store.platform)}`}>
                        {store.platform}
                      </span>
                    </td>
                    {columns.map(c => (
                      <td key={c.key} className={`px-1 py-1 border-r overflow-hidden ${c.w || 'max-w-[180px]'}`} style={c.style}>
                        {renderCell(store, c.key, store[c.key] as string | null, {
                          isPassword: c.key === "password",
                          isLink: (c.key as string) === "link",
                        })}
                      </td>
                    ))}
                    <td className="px-1 py-1 border-r text-center relative">
                      {deptDropdownId === store.id ? (
                        <div className="absolute z-50 top-0 left-0 bg-popover text-popover-foreground border rounded shadow-lg p-2 min-w-[120px]">
                          {(["celular", "mala", "eletro"] as const).map(d => {
                            const currentDeps = getStoreDepartments(store);
                            const isActive = currentDeps.includes(d);
                            return (
                              <label key={d} className="flex items-center gap-1.5 text-[10px] cursor-pointer py-0.5 hover:bg-muted/50 rounded px-1">
                                <input type="checkbox" checked={isActive} onChange={() => {
                                  const newDeps = isActive ? currentDeps.filter(x => x !== d) : [...currentDeps, d];
                                  setDeptMut.mutate({ storeId: store.id, departments: newDeps as any });
                                  setDeptDropdownId(null);
                                }} className="h-3 w-3" />
                                <span className={d === 'celular' ? 'text-blue-700 dark:text-blue-400' : d === 'mala' ? 'text-orange-700 dark:text-orange-400' : 'text-emerald-700 dark:text-emerald-400'}>
                                  {d === 'celular' ? 'Celular' : d === 'mala' ? 'Mala' : 'Eletro'}
                                </span>
                              </label>
                            );
                          })}
                          <button onClick={() => setDeptDropdownId(null)} className="text-[9px] text-muted-foreground mt-1 w-full text-center hover:text-foreground">fechar</button>
                        </div>
                      ) : (
                        <button onClick={() => setDeptDropdownId(store.id)} className="cursor-pointer hover:bg-muted/50 rounded px-1 py-0.5" title="Clique para alterar departamento">
                          {(() => {
                            const dept = getDepartments(store);
                            if (dept.includes("+")) return <span className="text-[10px] font-medium text-purple-700 dark:text-purple-400">{dept}</span>;
                            if (dept.includes("Cel")) return <span className="text-[10px] font-medium text-blue-700 dark:text-blue-400">{dept}</span>;
                            if (dept.includes("Mala")) return <span className="text-[10px] font-medium text-orange-700 dark:text-orange-400">{dept}</span>;
                            if (dept.includes("Eletro")) return <span className="text-[10px] font-medium text-emerald-700 dark:text-emerald-400">{dept}</span>;
                            return <span className="text-[10px] text-muted-foreground">—</span>;
                          })()}
                        </button>
                      )}
                    </td>
                    <td className="px-1 py-1 border-r text-center">
                      {hasPricing(store) ? (
                        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-green-700 dark:text-green-400"><CheckCircle2 className="h-3 w-3" /> Sim</span>
                      ) : (
                        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-red-600 dark:text-red-400"><X className="h-3 w-3" /> Não</span>
                      )}
                    </td>
                    <td className="px-1 py-1 border-r text-center">
                      {hasIntegration(store) ? (
                        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-green-700 dark:text-green-400"><CheckCircle2 className="h-3 w-3" /> Sim</span>
                      ) : (
                        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-red-600 dark:text-red-400"><X className="h-3 w-3" /> Não</span>
                      )}
                    </td>
                    <td className="px-1 py-1 text-center">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-5 w-5 text-destructive"
                        onClick={() => {
                          if (confirm(`Remover "${store.accountName || store.platform}"?`)) deleteMut.mutate({ id: store.id });
                        }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </Fragment>
            ))}
            {filteredStores.length === 0 && (
              <tr>
                <td colSpan={columns.length + statusColumns.length + 2} className="text-center py-8 text-muted-foreground">
                  {searchText || filterPlatform !== "all" ? "Nenhuma loja encontrada com os filtros aplicados" : "Nenhuma loja cadastrada"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
