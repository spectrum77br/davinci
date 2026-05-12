import { useState, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { trpc } from "@/lib/trpc";
import { toast } from "sonner";
import { Search, Loader2, AlertCircle, DollarSign, Send } from "lucide-react";

type CatalogListing = {
  integrationId: number;
  integrationName: string;
  id: string;
  title: string;
  sku: string;
  price: number | undefined;
  variationId?: string;
  catalogProductId: string;
};

export function CatalogMLTable() {
  const [search, setSearch] = useState("");
  const [editingCell, setEditingCell] = useState<{ sku: string; integrationId: number } | null>(null);
  const [editPrice, setEditPrice] = useState("");

  // Fetch catalog listings
  const catalogQuery = trpc.pricing.getCatalogListings.useQuery();
  const pushCatalogPriceMut = trpc.pricing.pushCatalogPrice.useMutation({
    onSuccess: () => {
      toast.success("Preço atualizado com sucesso!");
      setEditingCell(null);
      catalogQuery.refetch();
    },
    onError: (err) => toast.error(`Erro: ${err.message}`),
  });

  const catalogs = catalogQuery.data?.catalogs ?? [];

  // Group by SKU
  const groupedBySku = useMemo(() => {
    const groups: Record<string, { sku: string; title: string; listings: CatalogListing[] }> = {};
    
    for (const catalog of catalogs) {
      if (!groups[catalog.sku]) {
        groups[catalog.sku] = { sku: catalog.sku, title: catalog.title, listings: [] };
      }
      groups[catalog.sku].listings.push(catalog);
    }

    return Object.values(groups).sort((a, b) => a.sku.localeCompare(b.sku));
  }, [catalogs]);

  // Filter by search
  const filtered = useMemo(() => {
    if (!search) return groupedBySku;
    const q = search.toLowerCase();
    return groupedBySku.filter(g => g.sku.toLowerCase().includes(q) || g.title.toLowerCase().includes(q));
  }, [groupedBySku, search]);

  // Get unique integration names
  const integrationNames = useMemo(() => {
    const names = new Set<string>();
    for (const catalog of catalogs) {
      names.add(catalog.integrationName);
    }
    return Array.from(names).sort();
  }, [catalogs]);

  const handlePushPrice = async () => {
    if (!editingCell) return;
    const price = parseFloat(editPrice);
    if (isNaN(price) || price <= 0) {
      toast.error("Preço inválido");
      return;
    }

    // Find the listing
    const listing = catalogs.find(
      c => c.sku === editingCell.sku && c.integrationId === editingCell.integrationId
    );
    if (!listing) return;

    await pushCatalogPriceMut.mutateAsync({
      integrationId: listing.integrationId,
      itemId: listing.id,
      variationId: listing.variationId,
      price,
    });
  };

  if (catalogQuery.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6 text-center">
          <Loader2 className="h-6 w-6 animate-spin mx-auto mb-2" />
          Carregando catálogos...
        </CardContent>
      </Card>
    );
  }

  if (catalogs.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6 text-center text-muted-foreground">
          <AlertCircle className="h-6 w-6 mx-auto mb-2 opacity-50" />
          Nenhum anúncio de catálogo encontrado
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Buscar por SKU ou título..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
      </div>

      {/* Table */}
      <div className="border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">SKU</th>
                <th className="px-4 py-2 text-left font-semibold">Título</th>
                {integrationNames.map((name) => (
                  <th key={name} className="px-4 py-2 text-center font-semibold text-xs">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((group) => (
                <tr key={group.sku} className="border-t hover:bg-muted/50">
                  <td className="px-4 py-2 font-mono text-xs font-semibold">{group.sku}</td>
                  <td className="px-4 py-2 text-xs truncate max-w-xs" title={group.title}>
                    {group.title}
                  </td>
                  {integrationNames.map((integrationName) => {
                    const listing = group.listings.find(l => l.integrationName === integrationName);
                    return (
                      <td key={integrationName} className="px-4 py-2 text-center">
                        {listing ? (
                          <button
                            onClick={() => {
                              setEditingCell({ sku: group.sku, integrationId: listing.integrationId });
                              setEditPrice(listing.price?.toString() ?? "");
                            }}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-blue-100 text-blue-700 hover:bg-blue-200 text-xs font-semibold"
                          >
                            <DollarSign className="h-3 w-3" />
                            {listing.price ? `R$${listing.price.toFixed(2)}` : "N/A"}
                          </button>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit Dialog */}
      <Dialog open={!!editingCell} onOpenChange={(open) => !open && setEditingCell(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Atualizar Preço de Catálogo</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">SKU</label>
              <p className="text-sm text-muted-foreground font-mono">{editingCell?.sku}</p>
            </div>
            <div>
              <label className="text-sm font-medium">Novo Preço (R$)</label>
              <Input
                type="number"
                step="0.01"
                min="0"
                value={editPrice}
                onChange={(e) => setEditPrice(e.target.value)}
                placeholder="0.00"
                autoFocus
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingCell(null)}>
              Cancelar
            </Button>
            <Button onClick={handlePushPrice} disabled={pushCatalogPriceMut.isPending}>
              {pushCatalogPriceMut.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Atualizando...
                </>
              ) : (
                <>
                  <Send className="h-4 w-4 mr-2" />
                  Atualizar
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Summary */}
      <div className="text-xs text-muted-foreground">
        Mostrando {filtered.length} de {groupedBySku.length} produtos de catálogo
      </div>
    </div>
  );
}
