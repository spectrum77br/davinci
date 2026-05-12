import { z } from "zod";
import { COOKIE_NAME } from "@shared/const";
import { getSessionCookieOptions } from "./_core/cookies";
import { systemRouter } from "./_core/systemRouter";
import { publicProcedure, protectedProcedure, router } from "./_core/trpc";
import { TRPCError } from "@trpc/server";
import { integrations } from "../drizzle/schema";
import { eq } from "drizzle-orm";
import {
  getIntegrationsByUserId, getIntegrationById,
  createIntegration, updateIntegration, deleteIntegration,
  getProductsByUserId, getProductById,
  createProduct, updateProduct, deleteProduct,
  getSyncLogs, getSyncStats,
  getAlertsByUserId, getUnreadAlertsCount, getLastDailySyncAlert,
  markAlertAsRead, markAllAlertsAsRead,
  getUserSettings, upsertUserSettings,
  addToSyncQueue,
  getListingsByUserId, upsertListing, upsertListingsBulk, deleteListing,
  getListingRequestsByUserId, createListingRequest, updateListingRequestStatus,
  getProductLinksByUserId, createProductLink, createProductLinksBulk, deleteProductLink,
  getStockDiscrepancies,
  getPricingAccounts, createPricingAccount, updatePricingAccount, deletePricingAccount,
  getPricingProducts, createPricingProduct, createPricingProductsBulk, updatePricingProduct, deletePricingProduct,
  getPricingOverrides, upsertPricingOverride, deletePricingOverride,
  getStoreInfoByUserId, createStoreInfo, updateStoreInfo, deleteStoreInfo,
  getOwnerId,
} from "./db";
import { importListingsFromPlatform } from "./services/listingService";
import { BlingService } from "./services/bling";
import { ShopeeService } from "./services/shopee";
import { AmazonService } from "./services/amazon";
import { MercadoLivreService } from "./services/mercadolivre";
import { TikTokService } from "./services/tiktok";
import { testIntegrationConnection, syncAllProducts, syncSingleProduct, syncProductSelected } from "./services/syncService";
import { setSyncAllRunning } from "./services/syncLock";
import { createSyncJob, getJob, getActiveJobForUser, updateJobProgress } from "./services/syncJobManager";
import { ensureValidShopeeToken } from "./services/shopeeTokenRefresh";
import { backfillNamesFromBling } from "./services/autoImportLink";
import { notifyOwner } from "./_core/notification";
import { parseAuditSpreadsheet, getSpreadsheetSheets, runAudit, AuditResult, AuditParsedData, mapAccountToIntegration } from "./services/auditService";
import { storagePut } from "./storage";

// ── Integrations Router ────────────────────────────────────────────────────
const integrationsRouter = router({
  list: protectedProcedure.query(async ({ ctx }) => {
    const items = await getIntegrationsByUserId(await getOwnerId());
    return items.map(i => ({ ...i, credentials: undefined })); // Nunca expõe credenciais
  }),

  get: protectedProcedure.input(z.object({ id: z.number() })).query(async ({ ctx, input }) => {
    const items = await getIntegrationsByUserId(await getOwnerId());
    const item = items.find(i => i.id === input.id);
    if (!item) throw new Error("Integração não encontrada");
    return { ...item, credentials: undefined };
  }),

  create: protectedProcedure.input(z.object({
    platform: z.enum(["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]),
    name: z.string().min(1),
    credentials: z.record(z.string(), z.string()),
  })).mutation(async ({ ctx, input }) => {
    // Trim all credential values to avoid whitespace issues
    const trimmedCredentials = Object.fromEntries(
      Object.entries(input.credentials).map(([k, v]) => [k, v.trim()])
    );
    const item = await createIntegration({
      userId: await getOwnerId(),
      platform: input.platform,
      name: input.name,
      credentials: JSON.stringify(trimmedCredentials),
      status: "disconnected",
      isActive: true,
    });
    return { ...item, credentials: undefined };
  }),

  update: protectedProcedure.input(z.object({
    id: z.number(),
    name: z.string().optional(),
    credentials: z.record(z.string(), z.string()).optional(),
    isActive: z.boolean().optional(),
  })).mutation(async ({ ctx, input }) => {
    const { id, credentials, ...rest } = input;
    const updateData: Record<string, unknown> = { ...rest };
    if (credentials) updateData.credentials = JSON.stringify(credentials);
    await updateIntegration(id, await getOwnerId(), updateData as any);
    return { success: true };
  }),

  delete: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await deleteIntegration(input.id, await getOwnerId());
    return { success: true };
  }),

  testConnection: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    const integration = await getIntegrationById(input.id, await getOwnerId());
    if (!integration) throw new Error("Integração não encontrada");
    const result = await testIntegrationConnection(integration);
    await updateIntegration(input.id, await getOwnerId(), {
      status: result.success ? "connected" : "error",
      errorMessage: result.success ? null : result.message,
      lastSyncAt: result.success ? new Date() : undefined,
    });
    return result;
  }),
});

// ── Products Router ────────────────────────────────────────────────────────
const productsRouter = router({
  list: protectedProcedure.query(async ({ ctx }) => {
    const prods = await getProductsByUserId(await getOwnerId());
    // Enrich with integration names for multi-account display
    const integs = await getIntegrationsByUserId(await getOwnerId());
    const integMap = new Map(integs.map(i => [i.id, i.name]));
    // Enrich with listing titles for search by product name
    const allListings = await getListingsByUserId(await getOwnerId());
    // Build map: productId → all unique listing titles (primary match)
    const titlesByProductId = new Map<number, Set<string>>();
    // Build map: sku (lowercase) → all unique listing titles (fallback)
    const titlesBySku = new Map<string, Set<string>>();
    for (const l of allListings) {
      if (l.productId) {
        if (!titlesByProductId.has(l.productId)) titlesByProductId.set(l.productId, new Set());
        titlesByProductId.get(l.productId)!.add(l.title);
      }
      if (l.sku) {
        const skuKey = l.sku.toLowerCase();
        if (!titlesBySku.has(skuKey)) titlesBySku.set(skuKey, new Set());
        titlesBySku.get(skuKey)!.add(l.title);
      }
    }
    return prods.map(p => {
      // Prefer titles matched by productId, fallback to SKU
      const titlesSet = titlesByProductId.get(p.id) ?? titlesBySku.get(p.sku.toLowerCase());
      const allTitles = titlesSet ? Array.from(titlesSet) : [];
      return {
        ...p,
        shopeeIntegrationName: p.shopeeIntegrationId ? integMap.get(p.shopeeIntegrationId) ?? null : null,
        amazonIntegrationName: p.amazonIntegrationId ? integMap.get(p.amazonIntegrationId) ?? null : null,
        mercadolivreIntegrationName: p.mercadolivreIntegrationId ? integMap.get(p.mercadolivreIntegrationId) ?? null : null,
        listingTitle: allTitles.length > 0 ? allTitles[0] : null,
        allListingTitles: allTitles,
      };
    });
  }),

  get: protectedProcedure.input(z.object({ id: z.number() })).query(async ({ ctx, input }) => {
    const items = await getProductsByUserId(await getOwnerId());
    const item = items.find(p => p.id === input.id);
    if (!item) throw new Error("Produto não encontrado");
    return item;
  }),

  create: protectedProcedure.input(z.object({
    sku: z.string().min(1),
    name: z.string().min(1),
    blingId: z.string().optional(),
    shopeeId: z.string().optional(),
    amazonId: z.string().optional(),
    mercadolivreId: z.string().optional(),
    lowStockThreshold: z.number().default(5),
  })).mutation(async ({ ctx, input }) => {
    return createProduct({ ...input, userId: await getOwnerId() });
  }),

  update: protectedProcedure.input(z.object({
    id: z.number(),
    sku: z.string().optional(),
    name: z.string().optional(),
    blingId: z.string().optional(),
    shopeeId: z.string().optional(),
    amazonId: z.string().optional(),
    mercadolivreId: z.string().optional(),
    lowStockThreshold: z.number().optional(),
    isActive: z.boolean().optional(),
  })).mutation(async ({ ctx, input }) => {
    const { id, ...data } = input;
    await updateProduct(id, await getOwnerId(), data as any);
    return { success: true };
  }),

  delete: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await deleteProduct(input.id, await getOwnerId());
    return { success: true };
  }),

  bulkDelete: protectedProcedure.input(z.object({ ids: z.array(z.number()).min(1) })).mutation(async ({ ctx, input }) => {
    const ownerId = await getOwnerId();
    let deleted = 0;
    for (const id of input.ids) {
      await deleteProduct(id, ownerId);
      deleted++;
    }
    return { success: true, deleted };
  }),

  deleteLink: protectedProcedure.input(z.object({ linkId: z.number() })).mutation(async ({ ctx, input }) => {
    await deleteProductLink(input.linkId);
    return { success: true };
  }),

  fetchFromBling: protectedProcedure.query(async ({ ctx }) => {
    // Busca todos os produtos do Bling para pré-visualização (sem salvar)
    const integrations = await getIntegrationsByUserId(await getOwnerId());
    const blingIntegration = integrations.find(i => i.platform === "bling" && i.status === "connected");
    if (!blingIntegration) throw new Error("Nenhuma integração com o Bling conectada. Configure e teste a conexão primeiro.");

    const credentials = JSON.parse(blingIntegration.credentials ?? "{}");
    const bling = new BlingService(
      { apiKey: credentials.apiKey ?? credentials.token ?? "", refreshToken: credentials.refreshToken, tokenExpiresAt: credentials.tokenExpiresAt ? Number(credentials.tokenExpiresAt) : undefined },
      blingIntegration.id
    );
    const blingProducts = await bling.getAllProducts();

    const existingProducts = await getProductsByUserId(await getOwnerId());
    const existingBySku = new Map(existingProducts.map(p => [p.sku.toLowerCase(), p]));
    const existingByBlingId = new Map(existingProducts.filter(p => p.blingId).map(p => [p.blingId!, p]));

    return blingProducts.map(bp => ({
      blingId: bp.id,
      sku: bp.codigo || `BLING-${bp.id}`,
      name: bp.descricao,
      stock: bp.estoque,
      situacao: bp.situacao,
      alreadyImported: existingBySku.has((bp.codigo || "").toLowerCase()) || (existingByBlingId.has(bp.id) && existingByBlingId.get(bp.id)!.sku.toLowerCase() === (bp.codigo || "").toLowerCase()),
      existingProductId: existingBySku.get((bp.codigo || "").toLowerCase())?.id ?? (existingByBlingId.has(bp.id) && existingByBlingId.get(bp.id)!.sku.toLowerCase() === (bp.codigo || "").toLowerCase() ? existingByBlingId.get(bp.id)!.id : undefined),
    }));
  }),

  importFromBling: protectedProcedure.input(z.object({
    selectedIds: z.array(z.string()), // IDs do Bling selecionados para importar
  })).mutation(async ({ ctx, input }) => {
    const integrations = await getIntegrationsByUserId(await getOwnerId());
    const blingIntegration = integrations.find(i => i.platform === "bling" && i.status === "connected");
    if (!blingIntegration) throw new Error("Nenhuma integração com o Bling conectada.");

    const credentials = JSON.parse(blingIntegration.credentials ?? "{}");
    const bling = new BlingService(
      { apiKey: credentials.apiKey ?? credentials.token ?? "", refreshToken: credentials.refreshToken, tokenExpiresAt: credentials.tokenExpiresAt ? Number(credentials.tokenExpiresAt) : undefined },
      blingIntegration.id
    );
    const blingProducts = await bling.getAllProducts();

    const selectedSet = new Set(input.selectedIds);
    const toImport = blingProducts.filter(bp => selectedSet.has(bp.id));

    const existingProducts = await getProductsByUserId(await getOwnerId());
    const existingBySku = new Map(existingProducts.map(p => [p.sku.toLowerCase(), p]));
    const existingByBlingId = new Map(existingProducts.filter(p => p.blingId).map(p => [p.blingId!, p]));

    let imported = 0;
    let updated = 0;
    let errors = 0;

    for (const bp of toImport) {
      try {
        const sku = bp.codigo || `BLING-${bp.id}`;
        const existing = existingByBlingId.get(bp.id) ?? existingBySku.get(sku.toLowerCase());
        if (existing) {
          await updateProduct(existing.id, await getOwnerId(), {
            sku,
            name: bp.descricao,
            blingId: bp.id,
            blingStock: bp.estoque,
          } as any);
          if (existing.sku !== sku) {
            console.log(`[importFromBling] SKU atualizado: ${existing.sku} → ${sku} (blingId: ${bp.id})`);
          }
          updated++;
        } else {
          await createProduct({
            userId: await getOwnerId(),
            sku,
            name: bp.descricao,
            blingId: bp.id,
            blingStock: bp.estoque,
            lowStockThreshold: 5,
          });
          imported++;
        }
      } catch (err: any) {
        errors++;
        console.error(`[importFromBling] Error importing ${bp.id}:`, err.message);
      }
    }

    return { imported, updated, errors, total: toImport.length };
  }),

  updateNamesFromBling: protectedProcedure.mutation(async ({ ctx }) => {
    const ownerId = await getOwnerId();
    const updatedCount = await backfillNamesFromBling(ownerId);
    const existingProducts = await getProductsByUserId(ownerId);
    return { updated: updatedCount, total: existingProducts.length };
  }),

  importCsv: protectedProcedure.input(z.object({
    // CSV content as string (parsed on frontend, sent as array of rows)
    rows: z.array(z.object({
      sku: z.string().min(1),
      name: z.string().min(1),
      blingId: z.string().optional(),
      shopeeId: z.string().optional(),
      amazonId: z.string().optional(),
      mercadolivreId: z.string().optional(),
      lowStockThreshold: z.number().optional(),
    })),
  })).mutation(async ({ ctx, input }) => {
    let imported = 0;
    let updated = 0;
    let errors = 0;
    const errorDetails: string[] = [];

    const existingProducts = await getProductsByUserId(await getOwnerId());
    const existingBySku = new Map(existingProducts.map(p => [p.sku.toLowerCase(), p]));

    for (const row of input.rows) {
      try {
        const existing = existingBySku.get(row.sku.toLowerCase());
        if (existing) {
          await updateProduct(existing.id, await getOwnerId(), {
            name: row.name,
            blingId: row.blingId,
            shopeeId: row.shopeeId,
            amazonId: row.amazonId,
            mercadolivreId: row.mercadolivreId,
            lowStockThreshold: row.lowStockThreshold ?? 5,
          } as any);
          updated++;
        } else {
          await createProduct({
            userId: await getOwnerId(),
            sku: row.sku,
            name: row.name,
            blingId: row.blingId,
            shopeeId: row.shopeeId,
            amazonId: row.amazonId,
            mercadolivreId: row.mercadolivreId,
            lowStockThreshold: row.lowStockThreshold ?? 5,
          });
          imported++;
        }
      } catch (err: any) {
        errors++;
        errorDetails.push(`SKU ${row.sku}: ${err.message}`);
      }
    }

    return { imported, updated, errors, errorDetails, total: input.rows.length };
  }),

  getProductLinks: protectedProcedure.query(async ({ ctx }) => {
    const links = await getProductLinksByUserId(await getOwnerId());
    // Enrich with integration names
    const integs = await getIntegrationsByUserId(await getOwnerId());
    const integMap = new Map(integs.map(i => [i.id, i.name]));
    return links.map(l => ({
      ...l,
      integrationName: integMap.get(l.integrationId) ?? null,
    }));
  }),

  // Start autoLink in background - returns jobId immediately
  startAutoLink: protectedProcedure.input(z.object({
    integrationIds: z.array(z.number()).optional(),
  }).optional()).mutation(async ({ ctx, input }) => {
    // Check if there's already a running autoLink job
    const activeJob = getActiveJobForUser(await getOwnerId());
    if (activeJob && activeJob.id.startsWith("autolink_")) {
      return { jobId: activeJob.id, alreadyRunning: true };
    }

    const myProducts = await getProductsByUserId(await getOwnerId());
    if (myProducts.length === 0) throw new Error("Nenhum produto encontrado. Importe seus produtos do Bling primeiro.");

    const integrations = await getIntegrationsByUserId(await getOwnerId());
    const filterIds = input?.integrationIds;
    const connected = integrations.filter(i => i.status === "connected" && (!filterIds || filterIds.length === 0 || filterIds.includes(i.id)));

    // Create a job with prefix "autolink_" to distinguish from sync jobs
    const job = createSyncJob(await getOwnerId(), connected.length, "autolink_");
    updateJobProgress(job.id, { status: "running", currentSku: "Iniciando vinculação..." });

    // Run in background (don't await)
    (async () => {
      try {
        const results: { platform: string; linked: number; notFound: number; skipped: number; errors: string[]; }[] = [];
        const productsBySku = new Map(myProducts.map(p => [p.sku.toLowerCase().trim(), p]));
        const existingLinks = await getProductLinksByUserId(await getOwnerId());
        // For Amazon: include integrationId because the same SKU exists in multiple accounts (KFA, Poofy)
        const existingLinkSet = new Set(existingLinks.map(l => {
          if (l.platform === 'amazon') {
            return `${l.productId}:amazon:${l.integrationId}:${l.externalId}:${l.variationId || ''}`;
          }
          return `${l.productId}:${l.platform}:${l.externalId}:${l.variationId || ''}`;
        }));

    let processedCount = 0;
    const totalIntegrations = connected.length;
    let runningLinked = 0;

    // ── Mercado Livre (percorre TODAS as contas ML conectadas) ─────────────
    const mlIntegrations = connected.filter(i => i.platform === "mercadolivre");
    for (const mlIntegration of mlIntegrations) {
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, currentSku: `Mercado Livre: ${mlIntegration.name}`, synced: runningLinked });
      const mlResult = { platform: `Mercado Livre (${mlIntegration.name})`, linked: 0, notFound: 0, skipped: 0, errors: [] as string[] };
      try {
        const creds = JSON.parse(mlIntegration.credentials ?? "{}");
        const ml = new MercadoLivreService(creds, mlIntegration.id);
        let offset = 0;
        let hasMore = true;
        console.log(`[AutoLink ML] Starting search for integration: ${mlIntegration.name}`);
        while (hasMore) {
          const mlProducts = await ml.getProducts(offset, 50);
          console.log(`[AutoLink ML] Fetched ${mlProducts.length} products from ML at offset ${offset}`);
          if (mlProducts.length === 0) { hasMore = false; break; }
          for (const mlp of mlProducts) {
            const sku = (mlp.sku || "").toLowerCase().trim();
            const product = productsBySku.get(sku);
            if (!product) { mlResult.notFound++; continue; }
            // Check if this specific link already exists
            const linkKey = `${product.id}:mercadolivre:${mlp.id}:${mlp.variationId || ''}`;
            if (existingLinkSet.has(linkKey)) { mlResult.skipped++; continue; }
            // Create link in product_links table (supports multiple per SKU)
            await createProductLink({
              userId: await getOwnerId(),
              productId: product.id,
              platform: "mercadolivre",
              integrationId: mlIntegration.id,
              externalId: mlp.id,
              variationId: mlp.variationId || null,
              listingType: mlp.listingType || null,
            });
            existingLinkSet.add(linkKey);
            // Also update the legacy fields (first link only)
            if (!product.mercadolivreId) {
              await updateProduct(product.id, await getOwnerId(), {
                mercadolivreId: mlp.id,
                mercadolivreIntegrationId: mlIntegration.id,
                mercadolivreVariationId: mlp.variationId || null,
              } as any);
              (product as any).mercadolivreId = mlp.id;
            }
            // Auto-save listing so title is searchable
            try {
              await upsertListing({
                userId: await getOwnerId(),
                platform: "mercadolivre",
                externalId: `${mlp.id}${mlp.variationId ? '_' + mlp.variationId : ''}`,
                sku: mlp.sku || "",
                title: mlp.title || "",
                stock: mlp.stock ?? 0,
                status: mlp.status === "active" ? "active" : "paused",
                productId: product.id,
              });
            } catch (e) { /* ignore listing upsert errors */ }
            mlResult.linked++;
            console.log(`[AutoLink] Linked SKU "${sku}" → ML[${mlIntegration.name}] item=${mlp.id}${mlp.variationId ? ` variation=${mlp.variationId}` : ''} type=${mlp.listingType || 'unknown'}`);
          }
          if (mlProducts.length < 50) hasMore = false;
          offset += 50;
          if (hasMore) await new Promise(r => setTimeout(r, 300));
        }
      } catch (err: any) {
        mlResult.errors.push(err.message);
      }
      results.push(mlResult);
      processedCount++;
      runningLinked += mlResult.linked;
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, synced: runningLinked });
      // Delay between ML accounts to avoid rate limit
      if (mlIntegrations.indexOf(mlIntegration) < mlIntegrations.length - 1) {
        console.log(`[AutoLink ML] Waiting 3s before next ML account...`);
        await new Promise(r => setTimeout(r, 3000));
      }
    }

    // ── Shopee (percorre TODAS as contas Shopee conectadas) ─────────────────
    // Agora usa product_links (como ML) para suportar múltiplas contas por produto
    const shopeeIntegrations = connected.filter(i => i.platform === "shopee");
    for (const shopeeIntegration of shopeeIntegrations) {
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, currentSku: `Shopee: ${shopeeIntegration.name}`, synced: runningLinked });
      const shopeeResult = { platform: `Shopee (${shopeeIntegration.name})`, linked: 0, notFound: 0, skipped: 0, errors: [] as string[] };
      const MAX_ACCOUNT_RETRIES = 3;
      let accountSuccess = false;
      for (let accountAttempt = 0; accountAttempt < MAX_ACCOUNT_RETRIES && !accountSuccess; accountAttempt++) {
        if (accountAttempt > 0) {
          const retryWait = 5000 * Math.pow(2, accountAttempt - 1);
          console.log(`[AutoLink Shopee] Retry ${accountAttempt}/${MAX_ACCOUNT_RETRIES} for ${shopeeIntegration.name}, waiting ${retryWait}ms...`);
          await new Promise(r => setTimeout(r, retryWait));
          shopeeResult.errors = [];
        }
        try {
          const rawCreds = JSON.parse(shopeeIntegration.credentials ?? "{}");
          const creds = await ensureValidShopeeToken(shopeeIntegration.id, rawCreds);
          const shopee = new ShopeeService(creds);
          let offset = 0;
          let hasMore = true;
          console.log(`[AutoLink Shopee] Starting search for integration: ${shopeeIntegration.name} (attempt ${accountAttempt + 1})`);
          while (hasMore) {
            const shopeeVariations = await shopee.getProductsWithVariations(offset, 50);
            if (shopeeVariations.length === 0) { hasMore = false; break; }
            for (const sv of shopeeVariations) {
              const sku = (sv.sku || "").toLowerCase().trim();
              const product = productsBySku.get(sku);
              if (!product) { shopeeResult.notFound++; continue; }
              // Check if this specific link already exists in product_links
              const linkKey = `${product.id}:shopee:${String(sv.itemId)}:${String(sv.modelId)}`;
              if (existingLinkSet.has(linkKey)) { shopeeResult.skipped++; continue; }
              // Create link in product_links table (supports multiple accounts per SKU)
              await createProductLink({
                userId: await getOwnerId(),
                productId: product.id,
                platform: "shopee",
                integrationId: shopeeIntegration.id,
                externalId: String(sv.itemId),
                variationId: String(sv.modelId),
                listingType: null,
              });
              existingLinkSet.add(linkKey);
              // Also update the legacy fields (first link only, for backwards compatibility)
              if (!product.shopeeId) {
                await updateProduct(product.id, await getOwnerId(), {
                  shopeeId: String(sv.itemId),
                  shopeeModelId: String(sv.modelId),
                  shopeeIntegrationId: shopeeIntegration.id,
                } as any);
                (product as any).shopeeId = String(sv.itemId);
                (product as any).shopeeModelId = String(sv.modelId);
                (product as any).shopeeIntegrationId = shopeeIntegration.id;
              }
              // Auto-save listing so title is searchable
              try {
                await upsertListing({
                  userId: await getOwnerId(),
                  platform: "shopee",
                  externalId: `${sv.itemId}_${sv.modelId}`,
                  sku: sv.sku || "",
                  title: sv.itemName || "",
                  stock: sv.stock ?? 0,
                  status: sv.status === "NORMAL" ? "active" : "paused",
                  productId: product.id,
                });
              } catch (e) { /* ignore listing upsert errors */ }
              shopeeResult.linked++;
              console.log(`[AutoLink] Linked SKU "${sku}" → Shopee[${shopeeIntegration.name}] item=${sv.itemId} model=${sv.modelId}`);
            }
            hasMore = false;
            offset += 50;
            const moreItems = await shopee.getProducts(offset, 1);
            if (moreItems.length > 0) hasMore = true;
            if (hasMore) await new Promise(r => setTimeout(r, 1000));
          }
          accountSuccess = true;
        } catch (err: any) {
          console.error(`[AutoLink Shopee] Error on ${shopeeIntegration.name} (attempt ${accountAttempt + 1}):`, err.message);
          shopeeResult.errors.push(`Tentativa ${accountAttempt + 1}: ${err.message}`);
        }
      }
      results.push(shopeeResult);
      processedCount++;
      runningLinked += shopeeResult.linked;
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, synced: runningLinked });
      // Delay between Shopee accounts to avoid rate limit (5s instead of 3s)
      if (shopeeIntegrations.indexOf(shopeeIntegration) < shopeeIntegrations.length - 1) {
        console.log(`[AutoLink Shopee] Waiting 5s before next Shopee account...`);
        await new Promise(r => setTimeout(r, 5000));
      }
    }

    // ── Amazon (percorre TODAS as contas Amazon conectadas) ──────────────────────
    // Agora usa product_links (como ML/Shopee) para suportar múltiplas contas
    // OTIMIZADO: usa bulk insert para performance (~1099 SKUs em segundos ao invés de minutos)
    const amazonIntegrations = connected.filter(i => i.platform === "amazon");
    for (const amazonIntegration of amazonIntegrations) {
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, currentSku: `Amazon: ${amazonIntegration.name}`, synced: runningLinked });
      const amazonResult = { platform: `Amazon (${amazonIntegration.name})`, linked: 0, notFound: 0, skipped: 0, errors: [] as string[] };
      try {
        const creds = JSON.parse(amazonIntegration.credentials ?? "{}");
        const amazon = new AmazonService(creds);
        const amazonProducts = await amazon.getInventory();
        const ownerId = await getOwnerId();
        
        // Collect new links and listings for bulk insert
        const newLinks: Parameters<typeof createProductLinksBulk>[0] = [];
        const newListings: Parameters<typeof upsertListingsBulk>[0] = [];
        
        for (const ap of amazonProducts) {
          const sku = (ap.sku || "").toLowerCase().trim();
          const product = productsBySku.get(sku);
          if (!product) { amazonResult.notFound++; continue; }
          // Check if this specific link already exists in product_links (include integrationId for Amazon)
          const linkKey = `${product.id}:amazon:${amazonIntegration.id}:${ap.sku}:`;
          if (existingLinkSet.has(linkKey)) { amazonResult.skipped++; continue; }
          // Collect for bulk insert
          newLinks.push({
            userId: ownerId,
            productId: product.id,
            platform: "amazon",
            integrationId: amazonIntegration.id,
            externalId: ap.sku,
            variationId: ap.asin || null,
            listingType: null,
          });
          existingLinkSet.add(linkKey);
          // Collect listing for bulk upsert
          newListings.push({
            userId: ownerId,
            platform: "amazon",
            externalId: ap.asin || ap.sku,
            sku: ap.sku || "",
            title: ap.title || "",
            stock: ap.stock ?? 0,
            status: ap.status === "Active" ? "active" : "inactive",
            productId: product.id,
          });
          // Also update the legacy fields (first link only, for backwards compatibility)
          if (!product.amazonId) {
            await updateProduct(product.id, ownerId, {
              amazonId: ap.sku,
              amazonIntegrationId: amazonIntegration.id,
            } as any);
            (product as any).amazonId = ap.sku;
            (product as any).amazonIntegrationId = amazonIntegration.id;
          }
          amazonResult.linked++;
        }
        
        // Bulk insert all new links at once (chunks of 100)
        if (newLinks.length > 0) {
          await createProductLinksBulk(newLinks);
          console.log(`[AutoLink Amazon] Bulk inserted ${newLinks.length} links for ${amazonIntegration.name}`);
        }
        // Bulk upsert all listings at once (chunks of 100)
        if (newListings.length > 0) {
          await upsertListingsBulk(newListings);
          console.log(`[AutoLink Amazon] Bulk upserted ${newListings.length} listings for ${amazonIntegration.name}`);
        }
      } catch (err: any) {
        amazonResult.errors.push(err.message);
      }
      results.push(amazonResult);
      processedCount++;
      runningLinked += amazonResult.linked;
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, synced: runningLinked });
    }

    // ── TikTok Shop (percorre TODAS as contas TikTok conectadas) ──────────────────
    const tiktokIntegrations = connected.filter(i => i.platform === "tiktok");
    for (const tiktokIntegration of tiktokIntegrations) {
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, currentSku: `TikTok: ${tiktokIntegration.name}`, synced: runningLinked });
      const tiktokResult = { platform: `TikTok (${tiktokIntegration.name})`, linked: 0, notFound: 0, skipped: 0, errors: [] as string[] };
      try {
        const creds = JSON.parse(tiktokIntegration.credentials ?? "{}");
        const tiktok = new TikTokService({
          appKey: creds.appKey || process.env.TIKTOK_APP_KEY || "",
          appSecret: creds.appSecret || process.env.TIKTOK_APP_SECRET || "",
          accessToken: creds.accessToken || "",
          shopCipher: creds.shopCipher || "",
        });
        let pageToken: string | undefined;
        let hasMore = true;
        let pageCount = 0;
        const MAX_PAGES = 20; // Limite de 20 páginas (2000 produtos com page_size=100)
        const startTime = Date.now();
        const TIMEOUT_MS = 120000; // 2 minutos máximo
        console.log(`[AutoLink TikTok] Starting search for integration: ${tiktokIntegration.name}, shopCipher: ${creds.shopCipher ? 'present' : 'MISSING'}`);
        if (!creds.shopCipher) {
          throw new Error("shop_cipher não encontrado. Reautorize o TikTok Shop.");
        }
        while (hasMore) {
          if (Date.now() - startTime > TIMEOUT_MS) {
            console.warn(`[AutoLink TikTok] Timeout atingido para ${tiktokIntegration.name}`);
            tiktokResult.errors.push("Timeout: processo excedeu 2 minutos");
            break;
          }
          const { products: tiktokProducts, nextPageToken } = await tiktok.searchProducts(100, pageToken);
          if (tiktokProducts.length === 0) { hasMore = false; break; }
          for (const tp of tiktokProducts) {
            for (const sku of tp.skus) {
              const sellerSku = (sku.sellerSku || "").toLowerCase().trim();
              const product = productsBySku.get(sellerSku);
              if (!product) { tiktokResult.notFound++; continue; }
              // Check if this specific link already exists in product_links
              const linkKey = `${product.id}:tiktok:${tp.productId}:${sku.id}`;
              if (existingLinkSet.has(linkKey)) { tiktokResult.skipped++; continue; }
              // Create link in product_links table
              await createProductLink({
                userId: await getOwnerId(),
                productId: product.id,
                platform: "tiktok",
                integrationId: tiktokIntegration.id,
                externalId: tp.productId,
                variationId: sku.id,
                listingType: null,
              });
              existingLinkSet.add(linkKey);
              // Also update the legacy fields (first link only)
              if (!product.tiktokId) {
                await updateProduct(product.id, await getOwnerId(), {
                  tiktokId: tp.productId,
                  tiktokSkuId: sku.id,
                  tiktokIntegrationId: tiktokIntegration.id,
                } as any);
                (product as any).tiktokId = tp.productId;
                (product as any).tiktokSkuId = sku.id;
                (product as any).tiktokIntegrationId = tiktokIntegration.id;
              }
              // Auto-save listing
              try {
                await upsertListing({
                  userId: await getOwnerId(),
                  platform: "tiktok",
                  externalId: `${tp.productId}_${sku.id}`,
                  sku: sku.sellerSku || "",
                  title: tp.title || "",
                  stock: sku.stock ?? 0,
                  status: "active",
                  productId: product.id,
                });
              } catch (e) { /* ignore listing upsert errors */ }
              tiktokResult.linked++;
              console.log(`[AutoLink] Linked SKU "${sellerSku}" → TikTok[${tiktokIntegration.name}] product=${tp.productId} sku=${sku.id}`);
            }
          }
          pageCount++;
          console.log(`[AutoLink TikTok] Page ${pageCount}: found ${tiktokProducts.length} products, linked=${tiktokResult.linked}, notFound=${tiktokResult.notFound}, skipped=${tiktokResult.skipped}`);
          if (!nextPageToken || pageCount >= MAX_PAGES) hasMore = false;
          pageToken = nextPageToken;
          if (hasMore) await new Promise(r => setTimeout(r, 300));
        }
      } catch (err: any) {
        console.error(`[AutoLink TikTok] Error on ${tiktokIntegration.name}:`, err.message);
        tiktokResult.errors.push(err.message);
      }
      results.push(tiktokResult);
      processedCount++;
      runningLinked += tiktokResult.linked;
      updateJobProgress(job.id, { processed: processedCount, total: totalIntegrations, synced: runningLinked });
    }

    const totalLinked = results.reduce((sum, r) => sum + r.linked, 0);
    const totalNotFound = results.reduce((sum, r) => sum + r.notFound, 0);
    const totalSkipped = results.reduce((sum, r) => sum + r.skipped, 0);

    if (totalLinked > 0) {
      await notifyOwner({
        title: "Vinculação automática concluída",
        content: `${totalLinked} produto(s) vinculados automaticamente entre Bling e marketplaces.`,
      });
    }

    // Backfill names from Bling for products with empty name (auto-populate Nome column)
    try {
      await backfillNamesFromBling(await getOwnerId());
    } catch (e) { /* ignore backfill errors */ }

    updateJobProgress(job.id, {
      status: "completed",
      synced: totalLinked,
      errors: results.reduce((sum, r) => sum + r.errors.length, 0),
      details: results.map(r => `${r.platform}: ${r.linked} vinculados, ${r.skipped} já existentes, ${r.notFound} não encontrados${r.errors.length > 0 ? `, erros: ${r.errors.join("; ")}` : ""}`),
      completedAt: new Date(),
    });
      } catch (err: any) {
        console.error("[AutoLink] Fatal error:", err.message);
        updateJobProgress(job.id, {
          status: "error",
          details: [`Erro fatal: ${err.message}`],
          completedAt: new Date(),
        });
      }
    })();

    return { jobId: job.id, alreadyRunning: false };
  }),

  // Poll autoLink progress (reuses sync job infrastructure)
  getAutoLinkProgress: protectedProcedure.input(z.object({
    jobId: z.string(),
  })).query(async ({ ctx, input }) => {
    const job = getJob(input.jobId);
    if (!job || job.userId !== await getOwnerId()) return null;
    return {
      id: job.id,
      status: job.status,
      total: job.total,
      processed: job.processed,
      synced: job.synced,
      errors: job.errors,
      currentSku: job.currentSku,
      details: job.details,
      completedAt: job.completedAt,
    };
  }),

  // ── Vincular Manual Amazon ──
  manualLinkAmazon: protectedProcedure.input(z.object({
    productId: z.number(),
    integrationId: z.number(),
  })).mutation(async ({ ctx, input }) => {
    const product = await getProductById(input.productId, await getOwnerId());
    if (!product) {
      throw new TRPCError({ code: "NOT_FOUND", message: "Produto não encontrado" });
    }
    const integration = await getIntegrationById(input.integrationId, await getOwnerId());
    if (!integration || integration.platform !== "amazon") {
      throw new TRPCError({ code: "NOT_FOUND", message: "Integração Amazon não encontrada" });
    }
    // Check if link already exists
    const existingLinks = await getProductLinksByUserId(await getOwnerId());
    const alreadyLinked = existingLinks.find(
      l => l.productId === input.productId && l.integrationId === input.integrationId && l.platform === "amazon"
    );
    if (alreadyLinked) {
      return { success: true, message: "Produto já vinculado a esta conta Amazon", alreadyLinked: true };
    }
    // Use the product SKU as the Amazon externalId (seller-sku)
    const sku = product.sku;
    if (!sku) {
      throw new TRPCError({ code: "BAD_REQUEST", message: "Produto não tem SKU definido" });
    }
    await createProductLink({
      userId: await getOwnerId(),
      productId: input.productId,
      platform: "amazon",
      integrationId: input.integrationId,
      externalId: sku,
      variationId: null,
      listingType: null,
    });
    // Also update legacy fields if not set
    if (!product.amazonId) {
      await updateProduct(input.productId, await getOwnerId(), {
        amazonId: sku,
        amazonIntegrationId: input.integrationId,
      } as any);
    }
    console.log(`[ManualLink] Linked SKU "${sku}" → Amazon[${integration.name}] (manual)`);
    return { success: true, message: `Produto ${sku} vinculado à Amazon (${integration.name})`, alreadyLinked: false };
  }),
});

// ── Sync Router ────────────────────────────────────────────────────────────
const syncRouter = router({
  // Async sync - returns jobId immediately, processes in background
  startSyncAll: protectedProcedure.input(z.object({
    integrationIds: z.array(z.number()).optional(),
    productIds: z.array(z.number()).optional(),
  }).optional()).mutation(async ({ ctx, input }) => {
    // Check if there's already a running job
    const activeJob = getActiveJobForUser(await getOwnerId());
    if (activeJob) {
      return { jobId: activeJob.id, alreadyRunning: true };
    }

    // Count active products to set total
    const products = await getProductsByUserId(await getOwnerId());
    const targetProducts = input?.productIds && input.productIds.length > 0
      ? products.filter(p => input.productIds!.includes(p.id) && p.isActive)
      : products.filter(p => p.isActive);
    const job = createSyncJob(await getOwnerId(), targetProducts.length);

    // Start sync in background (don't await)
    // Lock: pause webhooks/polling during full sync to avoid concurrent DB access
    setSyncAllRunning(true);
    syncAllProducts(await getOwnerId(), input?.integrationIds, (progress) => {
      updateJobProgress(job.id, {
        status: "running",
        processed: progress.processed,
        synced: progress.synced,
        errors: progress.errors,
        skipped: progress.skipped,
        skippedVerified: progress.skippedVerified,
        skippedClosed: progress.skippedClosed,
        currentSku: progress.currentSku,
        details: progress.details,
      });
    }, input?.productIds).then(async (result) => {
      setSyncAllRunning(false);
      updateJobProgress(job.id, {
        status: "completed",
        synced: result.synced,
        errors: result.errors,
        skipped: result.skipped,
        skippedVerified: result.skippedVerified,
        skippedClosed: result.skippedClosed,
        details: result.details,
        completedAt: new Date(),
      });
      // Build smart notification summary
      const parts: string[] = [];
      parts.push(`${result.synced} sincronizado(s)`);
      if (result.skippedVerified > 0) parts.push(`${result.skippedVerified} pulado(s) (estoque igual verificado)`);
      if (result.skippedClosed > 0) parts.push(`${result.skippedClosed} pulado(s) (anúncios encerrados/em revisão)`);
      if (result.errors > 0) parts.push(`${result.errors} erro(s) real(is)`);
      const summary = parts.join(", ");
      if (result.errors > 0) {
        const errorDetails = result.details.filter(d => d.startsWith("✗")).slice(0, 20).join("\n");
        await notifyOwner({
          title: "Erros na sincronização de estoque",
          content: `Resumo: ${summary}\n\nErros reais:\n${errorDetails}`,
        });
      } else if (result.skipped > 0) {
        await notifyOwner({
          title: "Sincronização concluída",
          content: `Resumo: ${summary}\n\nNenhum erro real. Os anúncios pulados são encerrados ou em revisão no Mercado Livre.`,
        });
      }
    }).catch((err) => {
      setSyncAllRunning(false);
      updateJobProgress(job.id, {
        status: "error",
        details: [`Erro fatal: ${err.message}`],
        completedAt: new Date(),
      });
    });

    return { jobId: job.id, alreadyRunning: false };
  }),

  // Poll sync progress
  getSyncProgress: protectedProcedure.input(z.object({
    jobId: z.string(),
  })).query(async ({ ctx, input }) => {
    const job = getJob(input.jobId);
    if (!job || job.userId !== await getOwnerId()) {
      return null;
    }
    return {
      id: job.id,
      status: job.status,
      total: job.total,
      processed: job.processed,
      synced: job.synced,
      errors: job.errors,
      skipped: job.skipped,
      currentSku: job.currentSku,
      details: job.status === 'completed' ? job.details.slice(-100) : job.details.slice(-20), // 100 when done, 20 during progress
    };
  }),

  // Keep legacy syncAll for backward compat (scheduler uses it)
  syncAll: protectedProcedure.input(z.object({
    integrationIds: z.array(z.number()).optional(),
  }).optional()).mutation(async ({ ctx, input }) => {
    const result = await syncAllProducts(await getOwnerId(), input?.integrationIds);
    // Build smart notification summary
    const parts: string[] = [];
    parts.push(`${result.synced} sincronizado(s)`);
    if (result.skipped > 0) parts.push(`${result.skipped} pulado(s) (anúncios encerrados/em revisão)`);
    if (result.errors > 0) parts.push(`${result.errors} erro(s) real(is)`);
    const summary = parts.join(", ");
    if (result.errors > 0) {
      const errorDetails = result.details.filter(d => d.startsWith("✗")).slice(0, 20).join("\n");
      await notifyOwner({
        title: "Erros na sincronização de estoque",
        content: `Resumo: ${summary}\n\nErros reais:\n${errorDetails}`,
      });
    } else if (result.skipped > 0) {
      await notifyOwner({
        title: "Sincronização concluída",
        content: `Resumo: ${summary}\n\nNenhum erro real. Os anúncios pulados são encerrados ou em revisão no Mercado Livre.`,
      });
    }
    return result;
  }),

  syncProductSelected: protectedProcedure.input(z.object({
    productId: z.number(),
    integrationIds: z.array(z.number()),
  })).mutation(async ({ ctx, input }) => {
    return syncProductSelected(await getOwnerId(), input.productId, input.integrationIds);
  }),

  syncProduct: protectedProcedure.input(z.object({
    productId: z.number(),
    platform: z.enum(["shopee", "amazon", "mercadolivre"]),
  })).mutation(async ({ ctx, input }) => {
    return syncSingleProduct(await getOwnerId(), input.productId, input.platform);
  }),

  getLogs: protectedProcedure.input(z.object({ limit: z.number().default(100) })).query(async ({ ctx, input }) => {
    return getSyncLogs(await getOwnerId(), input.limit);
  }),

  getStats: protectedProcedure.query(async ({ ctx }) => {
    return getSyncStats(await getOwnerId());
  }),
});

// ── Alerts Router ──────────────────────────────────────────────────────────
const alertsRouter = router({
  list: protectedProcedure.input(z.object({ limit: z.number().default(50) })).query(async ({ ctx, input }) => {
    return getAlertsByUserId(await getOwnerId(), input.limit);
  }),

  lastDailySync: protectedProcedure.query(async ({ ctx }) => {
    return getLastDailySyncAlert(await getOwnerId());
  }),

  unreadCount: protectedProcedure.query(async ({ ctx }) => {
    return getUnreadAlertsCount(await getOwnerId());
  }),

  markRead: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await markAlertAsRead(input.id, await getOwnerId());
    return { success: true };
  }),

  markAllRead: protectedProcedure.mutation(async ({ ctx }) => {
    await markAllAlertsAsRead(await getOwnerId());
    return { success: true };
  }),
});

// ── Settings Router ────────────────────────────────────────────────────────
const settingsRouter = router({
  get: protectedProcedure.query(async ({ ctx }) => {
    const settings = await getUserSettings(await getOwnerId());
    return settings ?? {
      syncIntervalMinutes: 15,
      lowStockThreshold: 5,
      emailNotifications: true,
      inAppNotifications: true,
      notifyOnSyncError: true,
      notifyOnLowStock: true,
      notifyOnDiscrepancy: true,
      autoSync: true,
      dailySyncTime: "00:00",
    };
  }),

  webhookUrl: protectedProcedure.query(async ({ ctx }) => {
    // Return the webhook URL for the user to configure in Bling
    return {
      url: `${process.env.VITE_APP_URL || 'https://stocksync-vkklepy7.manus.space'}/api/webhooks/bling`,
      instructions: [
        '1. Acesse developer.bling.com.br e entre no seu aplicativo',
        '2. Vá na aba "Webhooks"',
        '3. Adicione um servidor com a URL acima',
        '4. Ative o recurso "Estoque" com as ações "created" e "updated"',
        '5. Salve as configurações',
      ],
    };
  }),

  update: protectedProcedure.input(z.object({
    syncIntervalMinutes: z.number().min(5).max(1440).optional(),
    lowStockThreshold: z.number().min(0).optional(),
    emailNotifications: z.boolean().optional(),
    inAppNotifications: z.boolean().optional(),
    notifyOnSyncError: z.boolean().optional(),
    notifyOnLowStock: z.boolean().optional(),
    notifyOnDiscrepancy: z.boolean().optional(),
    autoSync: z.boolean().optional(),
    dailySyncTime: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/).optional(),
  })).mutation(async ({ ctx, input }) => {
    return upsertUserSettings(await getOwnerId(), input as any, `user_ui (${ctx.user.name ?? ctx.user.openId})`);
  }),
});

// ── Listings Router ──────────────────────────────────────────────────────
const listingsRouter = router({
  list: protectedProcedure.input(z.object({
    platform: z.enum(["bling", "shopee", "amazon", "mercadolivre"]).optional(),
    status: z.string().optional(),
    search: z.string().optional(),
  }).optional()).query(async ({ ctx, input }) => {
    return getListingsByUserId(await getOwnerId(), input ?? {});
  }),

  import: protectedProcedure.input(z.object({
    platform: z.enum(["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]),
    integrationId: z.number(),
  })).mutation(async ({ ctx, input }) => {
    const result = await importListingsFromPlatform(await getOwnerId(), input.integrationId, input.platform);
    return result;
  }),

  delete: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await deleteListing(input.id, await getOwnerId());
    return { success: true };
  }),

  // Solicitações
  listRequests: protectedProcedure.query(async ({ ctx }) => {
    return getListingRequestsByUserId(await getOwnerId());
  }),

  createRequest: protectedProcedure.input(z.object({
    platform: z.enum(["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]),
    productName: z.string().min(1),
    sku: z.string().optional(),
    description: z.string().optional(),
    requestedPrice: z.number().optional(),
    category: z.string().optional(),
    notes: z.string().optional(),
  })).mutation(async ({ ctx, input }) => {
    return createListingRequest({
      userId: await getOwnerId(),
      platform: input.platform,
      productName: input.productName,
      sku: input.sku,
      description: input.description,
      requestedPrice: input.requestedPrice,
      category: input.category,
      notes: input.notes,
      status: "pending",
    });
  }),

  updateRequestStatus: protectedProcedure.input(z.object({
    id: z.number(),
    status: z.enum(["pending", "in_progress", "completed", "rejected"]),
  })).mutation(async ({ ctx, input }) => {
    await updateListingRequestStatus(input.id, await getOwnerId(), input.status);    return { success: true };
  }),
});

// ── Audit Router ───────────────────────────────────────────────────────────────────────
// In-memory storage for audit data and results
const auditDataStore = new Map<string, { parsedData: AuditParsedData; fileUrl: string }>();
const auditResultsStore = new Map<string, AuditResult[]>();

const auditRouter = router({
  // Upload e parse da planilha Excel
  uploadSpreadsheet: protectedProcedure.input(z.object({
    fileBase64: z.string(),
    fileName: z.string(),
  })).mutation(async ({ ctx, input }) => {
    const buffer = Buffer.from(input.fileBase64, "base64");
    
    // Upload para S3
    const randomSuffix = Math.random().toString(36).substring(2, 8);
    const fileKey = `audit/${await getOwnerId()}/${input.fileName}-${randomSuffix}`;
    const { url } = await storagePut(fileKey, buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    
    // Listar abas disponíveis
    const sheets = getSpreadsheetSheets(buffer);
    
    return { fileUrl: url, sheets };
  }),

  // Parse de uma aba específica da planilha
  parseSheet: protectedProcedure.input(z.object({
    fileBase64: z.string(),
    sheetName: z.string(),
  })).mutation(async ({ ctx, input }) => {
    const buffer = Buffer.from(input.fileBase64, "base64");
    const parsedData = parseAuditSpreadsheet(buffer, input.sheetName);
    
    // Salvar em memória para uso posterior
    const auditId = `audit_${await getOwnerId()}_${Date.now()}`;
    auditDataStore.set(auditId, { parsedData, fileUrl: "" });
    
    // Resumo das colunas detectadas
    const accountSummary = parsedData.columns.reduce((acc, col) => {
      const key = `${col.platform}:${col.accountName}`;
      if (!acc[key]) acc[key] = { accountName: col.accountName, platform: col.platform, columns: 0 };
      acc[key].columns++;
      return acc;
    }, {} as Record<string, { accountName: string; platform: string; columns: number }>);
    
    return {
      auditId,
      totalProducts: parsedData.rows.length,
      totalColumns: parsedData.columns.length,
      accounts: Object.values(accountSummary),
      sheetName: parsedData.sheetName,
    };
  }),

  // Iniciar auditoria em background
  startAudit: protectedProcedure.input(z.object({
    auditId: z.string(),
  })).mutation(async ({ ctx, input }) => {
    const auditData = auditDataStore.get(input.auditId);
    if (!auditData) throw new Error("Dados da auditoria não encontrados. Faça o upload novamente.");
    
    const totalAccounts = new Set(auditData.parsedData.columns.map(c => `${c.platform}:${c.accountName}`)).size;
    const job = createSyncJob(await getOwnerId(), totalAccounts, "audit_");
    job.status = "running";
    
    // Executar em background
    (async () => {
      try {
        const results = await runAudit(await getOwnerId(), auditData.parsedData, job.id);
        auditResultsStore.set(job.id, results);
        updateJobProgress(job.id, { status: "completed", completedAt: new Date() });
      } catch (error: any) {
        console.error("[Audit] Error:", error?.message);
        updateJobProgress(job.id, { status: "error", details: [error?.message || "Erro desconhecido"] });
      }
    })();
    
    return { jobId: job.id };
  }),

  // Polling de progresso
  getProgress: protectedProcedure.input(z.object({
    jobId: z.string(),
  })).query(async ({ input }) => {
    const job = getJob(input.jobId);
    if (!job) return null;
    return {
      status: job.status,
      total: job.total,
      processed: job.processed,
      currentStep: job.currentSku || "",
      details: job.details,
    };
  }),

  // Buscar resultados da auditoria
  getResults: protectedProcedure.input(z.object({
    jobId: z.string(),
  })).query(async ({ input }) => {
    const results = auditResultsStore.get(input.jobId);
    if (!results) return null;
    
    // Resumo
    const summary = {
      total: results.length,
      ok: results.filter(r => r.divergenceType === "ok").length,
      priceMismatch: results.filter(r => r.divergenceType === "price_mismatch").length,
      missing: results.filter(r => r.divergenceType === "missing").length,
      paused: results.filter(r => r.divergenceType === "paused").length,
    };
    
    return { results, summary };
  }),

  // Corrigir preço de um anúncio individual
  fixPrice: protectedProcedure.input(z.object({
    accountName: z.string(),
    platform: z.string(),
    externalId: z.string(),
    expectedPrice: z.number(),
    sku: z.string().optional(),
  })).mutation(async ({ ctx, input }) => {
    const db = await (await import("./db")).getDb();
    if (!db) throw new Error("Database not available");

    const integrationId = await mapAccountToIntegration(await getOwnerId(), input.accountName, input.platform);
    if (!integrationId) throw new Error(`Conta "${input.accountName}" (${input.platform}) não encontrada`);

    const [integRecord] = await db.select().from(integrations).where(eq(integrations.id, integrationId));
    if (!integRecord) throw new Error("Integração não encontrada");

    const creds = JSON.parse(integRecord.credentials as string);

    // Arredondar preço para inteiro (marketplaces não aceitam centavos)
    const roundedPrice = Math.round(input.expectedPrice);

    if (input.platform === "mercadolivre") {
      const parts = input.externalId.split(":");
      const itemId = parts[0];
      const variationId = parts[1] || undefined;
      const service = new MercadoLivreService(creds, integRecord.id);
      await service.updatePrice(itemId, roundedPrice, variationId);
      return { success: true, message: `Preço atualizado para R$${roundedPrice} no ML (${itemId})` };
    }

    if (input.platform === "shopee") {
      const updatedCreds = await ensureValidShopeeToken(integRecord.id, creds);
      const parts = input.externalId.split(":");
      const itemId = parseInt(parts[0]);
      const modelId = parts[1] ? parseInt(parts[1]) : 0;
      const service = new ShopeeService(updatedCreds);
      await service.updatePromotionPrice(itemId, roundedPrice, modelId);
      return { success: true, message: `Preço de promoção atualizado para R$${roundedPrice} na Shopee (${itemId})` };
    }

    if (input.platform === "amazon") {
      if (!input.sku) throw new Error("SKU necessário para atualizar preço na Amazon");
      const service = new AmazonService(creds);
      await service.updatePrice(input.sku, roundedPrice);
      return { success: true, message: `Preço atualizado para R$${roundedPrice} na Amazon (${input.sku})` };
    }

    throw new Error(`Plataforma "${input.platform}" não suportada para correção de preço`);
  }),

  // Corrigir preços em massa
  fixPrices: protectedProcedure.input(z.object({
    items: z.array(z.object({
      accountName: z.string(),
      platform: z.string(),
      externalId: z.string(),
      expectedPrice: z.number(),
      sku: z.string().optional(),
    })),
  })).mutation(async ({ ctx, input }) => {
    const db = await (await import("./db")).getDb();
    if (!db) throw new Error("Database not available");

    const results: Array<{ externalId: string; accountName: string; success: boolean; message: string }> = [];

    // Cache de serviços por integração para evitar recriações
    const serviceCache = new Map<number, { platform: string; service: any }>(); 

    for (const item of input.items) {
      try {
        const integrationId = await mapAccountToIntegration(await getOwnerId(), item.accountName, item.platform);
        if (!integrationId) {
          results.push({ externalId: item.externalId, accountName: item.accountName, success: false, message: `Conta "${item.accountName}" não encontrada` });
          continue;
        }

        let cached = serviceCache.get(integrationId);
        if (!cached) {
          const [integRecord] = await db.select().from(integrations).where(eq(integrations.id, integrationId));
          if (!integRecord) {
            results.push({ externalId: item.externalId, accountName: item.accountName, success: false, message: "Integração não encontrada" });
            continue;
          }
          const creds = JSON.parse(integRecord.credentials as string);

          if (item.platform === "mercadolivre") {
            cached = { platform: "mercadolivre", service: new MercadoLivreService(creds, integRecord.id) };
          } else if (item.platform === "shopee") {
            const updatedCreds = await ensureValidShopeeToken(integRecord.id, creds);
            cached = { platform: "shopee", service: new ShopeeService(updatedCreds) };
          } else if (item.platform === "amazon") {
            cached = { platform: "amazon", service: new AmazonService(creds) };
          }
          if (cached) serviceCache.set(integrationId, cached);
        }

        if (!cached) {
          results.push({ externalId: item.externalId, accountName: item.accountName, success: false, message: `Plataforma "${item.platform}" não suportada` });
          continue;
        }

        // Arredondar preço para inteiro
        const roundedPrice = Math.round(item.expectedPrice);

        if (cached.platform === "mercadolivre") {
          const parts = item.externalId.split(":");
          await cached.service.updatePrice(parts[0], roundedPrice, parts[1] || undefined);
        } else if (cached.platform === "shopee") {
          const parts = item.externalId.split(":");
          await cached.service.updatePromotionPrice(parseInt(parts[0]), roundedPrice, parts[1] ? parseInt(parts[1]) : 0);
        } else if (cached.platform === "amazon") {
          if (!item.sku) throw new Error("SKU necessário para Amazon");
          await cached.service.updatePrice(item.sku, roundedPrice);
        }

        results.push({ externalId: item.externalId, accountName: item.accountName, success: true, message: `R$${roundedPrice}` });
      } catch (error: any) {
        results.push({ externalId: item.externalId, accountName: item.accountName, success: false, message: error?.message || "Erro desconhecido" });
      }
    }

    const successCount = results.filter(r => r.success).length;
    const errorCount = results.filter(r => !r.success).length;
    return { results, summary: { total: input.items.length, success: successCount, errors: errorCount } };
  }),
});

// ── Discrepancies Router ───────────────────────────────────────────────────────────────
const discrepanciesRouter = router({
  list: protectedProcedure.query(async ({ ctx }) => {
    return getStockDiscrepancies(await getOwnerId());
  }),
});
// ── Pricing Router ───────────────────────────────────────────────────────────────────────────────────
const pricingRouter = router({
  // Contas de pricing
  getAccounts: protectedProcedure
    .input(z.object({ department: z.enum(["celular", "mala", "eletro", "catalogo"]).optional() }).optional())
    .query(async ({ ctx, input }) => {
      const accounts = await getPricingAccounts(await getOwnerId());
      if (input?.department) {
        return accounts.filter(a => a.department === input.department);
      }
      return accounts;
    }),

  createAccount: protectedProcedure.input(z.object({
    name: z.string(),
    platform: z.enum(["mercadolivre", "shopee", "temu", "amazon", "aliexpress", "tiktok", "magalu"]),
    listingType: z.string().optional(),
    department: z.enum(["celular", "mala", "eletro", "catalogo"]),
    kitNumber: z.number().min(1).max(4),
    commission: z.string(),
    transport: z.string().optional(),
    margin1: z.string().nullable().optional(),
    shipping1: z.string().nullable().optional(),
    margin2: z.string().nullable().optional(),
    shipping2: z.string().nullable().optional(),
    margin3: z.string().nullable().optional(),
    shipping3: z.string().nullable().optional(),
    margin4: z.string().nullable().optional(),
    shipping4: z.string().nullable().optional(),
    margin5: z.string().nullable().optional(),
    shipping5: z.string().nullable().optional(),
    observation: z.string().nullable().optional(),
    observation2: z.string().nullable().optional(),
    observation3: z.string().nullable().optional(),
    integrationId: z.number().optional(),
    sortOrder: z.number().optional(),
  })).mutation(async ({ ctx, input }) => {
    const id = await createPricingAccount({ ...input, userId: await getOwnerId() });
    return { id };
  }),

  updateAccount: protectedProcedure.input(z.object({
    id: z.number(),
    name: z.string().optional(),
    platform: z.enum(["mercadolivre", "shopee", "temu", "amazon", "aliexpress", "tiktok", "magalu"]).optional(),
    listingType: z.string().optional(),
    kitNumber: z.number().min(1).max(4).optional(),
    commission: z.string().optional(),
    transport: z.string().optional(),
    margin1: z.string().nullable().optional(),
    shipping1: z.string().nullable().optional(),
    margin2: z.string().nullable().optional(),
    shipping2: z.string().nullable().optional(),
    margin3: z.string().nullable().optional(),
    shipping3: z.string().nullable().optional(),
    margin4: z.string().nullable().optional(),
    shipping4: z.string().nullable().optional(),
    margin5: z.string().nullable().optional(),
    shipping5: z.string().nullable().optional(),
    observation: z.string().nullable().optional(),
    observation2: z.string().nullable().optional(),
    observation3: z.string().nullable().optional(),
    integrationId: z.number().optional(),
    sortOrder: z.number().optional(),
    isActive: z.boolean().optional(),
  })).mutation(async ({ ctx, input }) => {
    const { id, ...data } = input;
    await updatePricingAccount(id, await getOwnerId(), data);
    return { success: true };
  }),

  deleteAccount: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await deletePricingAccount(input.id, await getOwnerId());
    return { success: true };
  }),

  // Auto-match integrationId para todas as pricing_accounts
  autoMatchIntegrations: protectedProcedure.mutation(async ({ ctx }) => {
    const userId = await getOwnerId();
    const accounts = await getPricingAccounts(userId);
    const results: Array<{ id: number; name: string; platform: string; integrationId: number | null; matched: boolean }> = [];
    for (const acc of accounts) {
      const matchedId = await mapAccountToIntegration(userId, acc.name, acc.platform);
      if (matchedId && matchedId !== acc.integrationId) {
        await updatePricingAccount(acc.id, userId, { integrationId: matchedId });
        results.push({ id: acc.id, name: acc.name, platform: acc.platform, integrationId: matchedId, matched: true });
      } else if (!matchedId && acc.integrationId) {
        // Already has one, keep it
        results.push({ id: acc.id, name: acc.name, platform: acc.platform, integrationId: acc.integrationId, matched: false });
      } else if (matchedId) {
        results.push({ id: acc.id, name: acc.name, platform: acc.platform, integrationId: matchedId, matched: false });
      } else {
        results.push({ id: acc.id, name: acc.name, platform: acc.platform, integrationId: null, matched: false });
      }
    }
    return { updated: results.filter(r => r.matched).length, total: accounts.length, results };
  }),

  // Produtos de pricingg
  getProducts: protectedProcedure
    .input(z.object({ department: z.enum(["celular", "mala", "eletro", "catalogo"]).optional() }).optional())
    .query(async ({ ctx, input }) => {
      const products = await getPricingProducts(await getOwnerId());
      if (input?.department) {
        return products.filter(p => p.department === input.department);
      }
      return products;
    }),

  createProduct: protectedProcedure.input(z.object({
    sku: z.string(),
    name: z.string(),
    department: z.enum(["celular", "mala", "eletro", "catalogo"]),
    productType: z.number().min(1).max(5),
    costKit1: z.string(),
    costKit2: z.string().nullable().optional(),
    costKit3: z.string().nullable().optional(),
    costKit4: z.string().nullable().optional(),
    description: z.string().optional(),
    model: z.string().optional(),
    ean: z.string().optional(),
    productId: z.number().optional(),
  })).mutation(async ({ ctx, input }) => {
    const id = await createPricingProduct({ ...input, userId: await getOwnerId() });
    return { id };
  }),

  importProducts: protectedProcedure.input(z.object({
    products: z.array(z.object({
      sku: z.string(),
      name: z.string(),
      department: z.enum(["celular", "mala", "eletro", "catalogo"]),
      productType: z.number().min(1).max(5),
      costKit1: z.string(),
      costKit2: z.string().nullable().optional(),
      costKit3: z.string().nullable().optional(),
      costKit4: z.string().nullable().optional(),
      description: z.string().optional(),
      model: z.string().optional(),
    })),
  })).mutation(async ({ ctx, input }) => {
    const ownerId = await getOwnerId();
    const data = input.products.map(p => ({ ...p, userId: ownerId }));
    await createPricingProductsBulk(data);
    return { count: data.length };
  }),

  updateProduct: protectedProcedure.input(z.object({
    id: z.number(),
    sku: z.string().optional(),
    name: z.string().optional(),
    productType: z.number().min(1).max(5).optional(),
    costKit1: z.string().optional(),
    costKit2: z.string().nullable().optional(),
    costKit3: z.string().nullable().optional(),
    costKit4: z.string().nullable().optional(),
    description: z.string().optional(),
    model: z.string().optional(),
    ean: z.string().nullable().optional(),
  })).mutation(async ({ ctx, input }) => {
    const { id, ...data } = input;
    await updatePricingProduct(id, await getOwnerId(), data);
    return { success: true };
  }),

  deleteProduct: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await deletePricingProduct(input.id, await getOwnerId());
    return { success: true };
  }),

  // Toggle catálogo: adiciona/remove produto do departamento "catalogo"
  toggleCatalog: protectedProcedure.input(z.object({
    productId: z.number(),
    enabled: z.boolean(),
  })).mutation(async ({ ctx, input }) => {
    const userId = await getOwnerId();
    const allProducts = await getPricingProducts(userId);
    const sourceProduct = allProducts.find(p => p.id === input.productId);
    if (!sourceProduct) throw new TRPCError({ code: "NOT_FOUND", message: "Produto não encontrado" });

    if (input.enabled) {
      // Check if already exists in catalogo with same SKU
      const existing = allProducts.find(p => p.department === "catalogo" && p.sku === sourceProduct.sku);
      if (existing) return { success: true, catalogProductId: existing.id };
      // Create copy in catalogo department
      const id = await createPricingProduct({
        userId,
        sku: sourceProduct.sku,
        name: sourceProduct.name,
        department: "catalogo",
        productType: sourceProduct.productType,
        costKit1: sourceProduct.costKit1,
        costKit2: sourceProduct.costKit2,
        costKit3: sourceProduct.costKit3,
        costKit4: sourceProduct.costKit4,
        description: sourceProduct.description,
        model: sourceProduct.model,
        ean: sourceProduct.ean,
        productId: sourceProduct.productId,
      });

      // Auto-copy ML accounts from source department to catalogo (if none exist yet)
      const allAccounts = await getPricingAccounts(userId);
      const catalogAccounts = allAccounts.filter(a => a.department === "catalogo");
      if (catalogAccounts.length === 0) {
        // Copy all mercadolivre accounts from source department
        const sourceDept = sourceProduct.department;
        const mlAccounts = allAccounts.filter(a => a.department === sourceDept && a.platform === "mercadolivre");
        for (const acc of mlAccounts) {
          await createPricingAccount({
            userId,
            name: acc.name,
            platform: acc.platform,
            listingType: acc.listingType,
            department: "catalogo",
            kitNumber: acc.kitNumber,
            commission: acc.commission,
            margin1: acc.margin1,
            shipping1: acc.shipping1,
            margin2: acc.margin2,
            shipping2: acc.shipping2,
            margin3: acc.margin3,
            shipping3: acc.shipping3,
            margin4: acc.margin4,
            shipping4: acc.shipping4,
            margin5: acc.margin5,
            shipping5: acc.shipping5,
            observation: acc.observation,
            observation2: acc.observation2,
            observation3: acc.observation3,
            integrationId: acc.integrationId,
            sortOrder: acc.sortOrder,
            isActive: acc.isActive,
          } as any);
        }
      }

      return { success: true, catalogProductId: id };
    } else {
      // Remove from catalogo by SKU
      const catalogProduct = allProducts.find(p => p.department === "catalogo" && p.sku === sourceProduct.sku);
      if (catalogProduct) {
        await deletePricingProduct(catalogProduct.id, userId);
      }
      return { success: true, catalogProductId: null };
    }
  }),

  // Overrides de preço
  getOverrides: protectedProcedure.query(async ({ ctx }) => {
    return getPricingOverrides(await getOwnerId());
  }),

  setOverride: protectedProcedure.input(z.object({
    pricingProductId: z.number(),
    pricingAccountId: z.number(),
    priceOverride: z.string().nullable().optional(),
    cellStatus: z.string().nullable().optional(),
  })).mutation(async ({ ctx, input }) => {
    const id = await upsertPricingOverride({ ...input, userId: await getOwnerId() });
    return { id };
  }),

  setCellStatus: protectedProcedure.input(z.object({
    pricingProductId: z.number(),
    pricingAccountId: z.number(),
    cellStatus: z.string().nullable(),
  })).mutation(async ({ ctx, input }) => {
    const id = await upsertPricingOverride({ ...input, userId: await getOwnerId() });
    return { id };
  }),

  removeOverride: protectedProcedure.input(z.object({
    pricingProductId: z.number(),
    pricingAccountId: z.number(),
  })).mutation(async ({ ctx, input }) => {
    await deletePricingOverride(input.pricingProductId, input.pricingAccountId, await getOwnerId());
    return { success: true };
  }),

  // Push de preço para o marketplace (atualiza o preço real no ML/Shopee/etc)
  pushPrice: protectedProcedure.input(z.object({
    pricingProductId: z.number(),
    pricingAccountId: z.number(),
    price: z.number(),
  })).mutation(async ({ ctx, input }) => {
    const db = await (await import("./db")).getDb();
    if (!db) throw new Error("Database not available");

    // 1. Buscar os product_links correspondentes
    const { findProductLinksForPricingPush } = await import("./db");
    const links = await findProductLinksForPricingPush(await getOwnerId(), input.pricingProductId, input.pricingAccountId);

    if (links.length === 0) {
      return {
        success: false,
        message: "Nenhum anúncio encontrado para este produto nesta conta. Verifique se os anúncios foram importados.",
        results: [],
      };
    }

    // 2. Buscar a pricing_account para obter integrationId
    const { getPricingAccounts } = await import("./db");
    const accounts = await getPricingAccounts(await getOwnerId());
    const account = accounts.find(a => a.id === input.pricingAccountId);
    if (!account || !account.integrationId) {
      return { success: false, message: "Conta sem integração vinculada", results: [] };
    }

    // 3. Buscar credenciais da integração
    const [integRecord] = await db.select().from(integrations).where(eq(integrations.id, account.integrationId));
    if (!integRecord) {
      return { success: false, message: "Integração não encontrada", results: [] };
    }
    const creds = JSON.parse(integRecord.credentials as string);
    const roundedPrice = Math.round(input.price);

    // 4. Atualizar preço em cada anúncio encontrado
    const results: Array<{ externalId: string; success: boolean; skipped?: boolean; message: string }> = [];
    let service: any = null;

    if (account.platform === "mercadolivre") {
      service = new MercadoLivreService(creds, integRecord.id);
    } else if (account.platform === "shopee") {
      const updatedCreds = await ensureValidShopeeToken(integRecord.id, creds);
      service = new ShopeeService(updatedCreds);
    } else if (account.platform === "amazon") {
      service = new AmazonService(creds);
    } else if (account.platform === "tiktok") {
      service = new TikTokService({
        appKey: creds.appKey || process.env.TIKTOK_APP_KEY || "",
        appSecret: creds.appSecret || process.env.TIKTOK_APP_SECRET || "",
        accessToken: creds.accessToken || "",
        shopCipher: creds.shopCipher || "",
      });
    }

    if (!service) {
      return { success: false, message: `Plataforma "${account.platform}" não suportada`, results: [] };
    }

    for (const link of links) {
      try {
        if (account.platform === "mercadolivre") {
          // ML: itens com variações precisam de PUT na variação, não no item
          const variationId = link.variationId || undefined;
          await service.updatePrice(link.externalId, roundedPrice, variationId);
          results.push({ externalId: link.externalId, success: true, message: `R$${roundedPrice}` });
        } else if (account.platform === "shopee") {
          const itemId = parseInt(link.externalId);
          // Use variationId from product_link as modelId (Shopee model_id)
          const modelId = link.variationId ? parseInt(link.variationId) : 0;
          await service.updatePromotionPrice(itemId, roundedPrice, modelId);
          results.push({ externalId: link.externalId, success: true, message: `R$${roundedPrice}` });
        } else if (account.platform === "amazon") {
          await service.updatePrice(link.productSku, roundedPrice);
          results.push({ externalId: link.externalId, success: true, message: `R$${roundedPrice}` });
        } else if (account.platform === "tiktok") {
          // TikTok: usa productId (externalId) + skuId (variationId) para atualizar preço
          const skuId = link.variationId || "";
          console.log(`[PricingPush][TikTok] Tentando atualizar preço: product=${link.externalId}, sku=${skuId}, price=${roundedPrice}, account=${account.name}`);
          if (!skuId) {
            results.push({ externalId: link.externalId, success: false, message: "SKU ID não encontrado no vínculo" });
          } else {
            // Enviar cada variação individualmente — com ativação automática se produto desativado
            await service.updatePriceWithActivation(link.externalId, skuId, roundedPrice);
            console.log(`[PricingPush][TikTok] Preço atualizado com sucesso: product=${link.externalId}, sku=${skuId}, price=${roundedPrice}`);
            results.push({ externalId: link.externalId, success: true, message: `R$${roundedPrice} (sku: ${link.productSku})` });
          }
        }
      } catch (error: any) {
        const errMsg = error?.message || "Erro desconhecido";
        // Anúncios encerrados/deletados/moderados são "pulados", não erros
        const isSkipped = errMsg.includes('encerrado') || errMsg.includes('moderação');
        if (isSkipped) {
          console.log(`[PricingPush] PULADO ${link.externalId} (${link.platform}): ${errMsg}`);
          results.push({ externalId: link.externalId, success: false, skipped: true, message: errMsg });
        } else {
          console.error(`[PricingPush] ERRO ${link.externalId} (${link.platform}): ${errMsg}`);
          results.push({ externalId: link.externalId, success: false, message: errMsg });
        }
      }
    }

    const successCount = results.filter(r => r.success).length;
    const skippedCount = results.filter(r => !r.success && r.skipped).length;
    const errorCount = results.filter(r => !r.success && !r.skipped).length;
    const errorDetails = results.filter(r => !r.success && !r.skipped).map(r => `${r.externalId}: ${r.message}`).join(' | ');
    console.log(`[PricingPush] Product ${input.pricingProductId} → Account ${input.pricingAccountId} (${account.name}): ${successCount} ok, ${skippedCount} pulados, ${errorCount} erros (R$${roundedPrice})${errorDetails ? ` — ${errorDetails}` : ''}`);

    const parts: string[] = [];
    if (successCount > 0) parts.push(`${successCount} atualizado(s)`);
    if (skippedCount > 0) parts.push(`${skippedCount} pulado(s) (encerrados)`);
    if (errorCount > 0) parts.push(`${errorCount} erro(s)`);

    return {
      success: errorCount === 0,
      message: parts.join(', ') || 'Nenhum anúncio encontrado',
      results,
    };
  }),


  // Buscar anúncios de catálogo do Mercado Livre de todas as contas
  getCatalogListings: protectedProcedure.query(async ({ ctx }) => {
    const userId = await getOwnerId();
    const { getIntegrationsByUserId } = await import("./db");
    const integrations = await getIntegrationsByUserId(userId);
    const mlIntegrations = integrations.filter(i => i.platform === "mercadolivre" && i.isActive);

    if (mlIntegrations.length === 0) {
      return { success: false, message: "Nenhuma conta Mercado Livre conectada", catalogs: [] };
    }

    const allCatalogs: Array<{
      integrationId: number;
      integrationName: string;
      id: string;
      title: string;
      sku: string;
      price: number | undefined;
      variationId?: string;
      catalogProductId: string;
    }> = [];

    for (const integration of mlIntegrations) {
      try {
        const creds = JSON.parse(integration.credentials as string);
        const { MercadoLivreService } = await import("./services/mercadolivre");
        const service = new MercadoLivreService(creds, integration.id);
        const catalogs = await service.getCatalogProducts();
        
        for (const catalog of catalogs) {
          allCatalogs.push({
            integrationId: integration.id,
            integrationName: integration.name,
            id: catalog.id,
            title: catalog.title,
            sku: catalog.sku,
            price: catalog.price,
            variationId: catalog.variationId,
            catalogProductId: catalog.catalogProductId,
          });
        }
      } catch (error: any) {
        console.error(`[Pricing] Erro ao buscar catálogos de ${integration.name}:`, error?.message);
      }
    }

    return { success: true, catalogs: allCatalogs };
  }),

  // Push de preço para anúncio de catálogo
  pushCatalogPrice: protectedProcedure.input(z.object({
    integrationId: z.number(),
    itemId: z.string(),
    variationId: z.string().optional(),
    price: z.number(),
  })).mutation(async ({ ctx, input }) => {
    const userId = await getOwnerId();
    const { getIntegrationById } = await import("./db");
    const integration = await getIntegrationById(input.integrationId, userId);
    
    if (!integration || integration.platform !== "mercadolivre") {
      return { success: false, message: "Integração Mercado Livre não encontrada" };
    }

    try {
      const creds = JSON.parse(integration.credentials as string);
      const { MercadoLivreService } = await import("./services/mercadolivre");
      const service = new MercadoLivreService(creds, integration.id);
      
      const roundedPrice = Math.round(input.price);
      await service.updatePrice(input.itemId, roundedPrice, input.variationId);
      
      console.log(`[CatalogPrice] ${integration.name}: Item ${input.itemId} -> R$${roundedPrice}`);
      return { success: true, message: `Preço atualizado para R$${roundedPrice}` };
    } catch (error: any) {
      console.error(`[CatalogPrice] Erro ao atualizar preço:`, error?.message);
      return { success: false, message: error?.message || "Erro ao atualizar preço" };
    }
  }),


  // Enviar relatório do push de preço via Telegram
  sendPushReport: protectedProcedure.input(z.object({
    sent: z.number(),
    errors: z.number(),
    noLinks: z.number(),
    skipped: z.number(),
    errorDetails: z.array(z.string()),
    noLinkDetails: z.array(z.string()),
    department: z.string(),
  })).mutation(async ({ input }) => {
    const { alertPricePushReport } = await import("./services/telegram");
    await alertPricePushReport(input);
    return { success: true };
  }),

  // Testar notificação Telegram
  testTelegram: protectedProcedure.mutation(async () => {
    const { sendTelegramAlert } = await import("./services/telegram");
    const ok = await sendTelegramAlert(
      `🟢 <b>Stock Sync Hub — Teste de notificação!</b>\n\nSe você está vendo esta mensagem, as notificações estão funcionando perfeitamente! ✅`
    );
    return { success: ok };
  }),

  // Auditoria de SKUs: SKUs com anúncios ativos que não estão na tabela de preços
  getSkuAudit: protectedProcedure.query(async ({ ctx }) => {
    const { getSkuAudit } = await import("./db");
    return getSkuAudit(await getOwnerId());
  }),

  // Dispensar SKU da auditoria
  dismissAuditSku: protectedProcedure.input(z.object({ sku: z.string() })).mutation(async ({ ctx, input }) => {
    const { dismissAuditSku } = await import("./db");
    return dismissAuditSku(await getOwnerId(), input.sku);
  }),

  // Restaurar SKU na auditoria
  undismissAuditSku: protectedProcedure.input(z.object({ sku: z.string() })).mutation(async ({ ctx, input }) => {
    const { undismissAuditSku } = await import("./db");
    return undismissAuditSku(await getOwnerId(), input.sku);
  }),

  // Comparador de preços: busca preços atuais de uma integração específica
  fetchActualPrices: protectedProcedure.input(z.object({
    integrationId: z.number(),
  })).mutation(async ({ ctx, input }) => {
    const db = await (await import("./db")).getDb();
    if (!db) throw new Error("Database not available");

    const [integRecord] = await db.select().from(integrations).where(eq(integrations.id, input.integrationId));
    if (!integRecord) throw new Error("Integração não encontrada");

    const creds = JSON.parse(integRecord.credentials);
    let listings: Array<{ title: string; sku: string; price?: number; status: string; externalId: string; listingType?: string }> = [];

    if (integRecord.platform === "mercadolivre") {
      const { MercadoLivreService } = await import("./services/mercadolivre");
      const service = new MercadoLivreService(creds, integRecord.id);
      const products = await service.getAllProducts();
      listings = products.map(p => ({
        title: p.title,
        sku: p.sku,
        price: p.price,
        status: p.status,
        externalId: p.variationId ? `${p.id}:${p.variationId}` : p.id,
        listingType: p.listingType,
      }));
    } else if (integRecord.platform === "shopee") {
      const { ShopeeService } = await import("./services/shopee");
      const { ensureValidShopeeToken } = await import("./services/shopeeTokenRefresh");
      const updatedCreds = await ensureValidShopeeToken(integRecord.id, creds);
      const service = new ShopeeService(updatedCreds);
      let offset = 0;
      while (true) {
        const batch = await service.getProductsWithVariations(offset, 50);
        for (const p of batch) {
          listings.push({
            title: p.modelName ? `${p.itemName} - ${p.modelName}` : p.itemName,
            sku: p.sku,
            price: p.price,
            status: p.status,
            externalId: p.modelId ? `${p.itemId}:${p.modelId}` : String(p.itemId),
          });
        }
        if (batch.length < 50) break;
        offset += 50;
        await new Promise(r => setTimeout(r, 1000));
      }
    } else if (integRecord.platform === "amazon") {
      const { AmazonService } = await import("./services/amazon");
      const service = new AmazonService(creds);
      const products = await service.getInventory();
      listings = products.map(p => ({
        title: p.title,
        sku: p.sku,
        price: p.price,
        status: p.status,
        externalId: p.asin,
      }));
    }

    // Retornar mapa de SKU → preço atual
    const priceMap: Record<string, { price: number | null; title: string; externalId: string; status: string }> = {};
    for (const listing of listings) {
      if (listing.sku) {
        // Usar SKU base (antes do ponto) como chave
        const skuBase = listing.sku.split(".")[0].toLowerCase();
        if (!priceMap[skuBase] || (listing.price && !priceMap[skuBase].price)) {
          priceMap[skuBase] = {
            price: listing.price ?? null,
            title: listing.title,
            externalId: listing.externalId,
            status: listing.status,
          };
        }
      }
    }
    return { priceMap, total: listings.length };
  }),

  // Cache de busca de concorrentes (5 minutos)
  searchCompetitorPrices: (() => {
    const cache = new Map<string, { data: any; timestamp: number }>();
    const CACHE_TTL = 5 * 60 * 1000; // 5 minutos

    return protectedProcedure.input(z.object({
      productName: z.string(),
      productSku: z.string().optional(),
    })).mutation(async ({ ctx, input }) => {
      const axios = (await import("axios")).default;

      interface CompetitorResult {
        platform: string;
        listingType: string;
        title: string;
        price: number;
        permalink: string;
        sellerId: string;
        condition: string;
      }

      // Limpar nome do produto para busca mais eficiente
      const colorWords = ['preto','branco','azul','verde','vermelho','amarelo','rosa','roxo','laranja','prata','dourado','cinza','coral','grafite','meia-noite','estelar','gold','silver','black','white','blue','green','red','purple','pink','midnight','starlight','natural','titanium','titânio','desert','deserto','teal'];
      const removeWords = ['kit','avulso','usado','avariada','lacre','sem lacre','com fone','fone','+','com','sem','de','da','do','e','ou','para','"','apple','samsung','xiaomi','motorola','realme','poco','redmi','hotwav','fossibot','doogee','oukitel','ulefone','blackview','umidigi','cubot'];
      let searchQuery = input.productName
        .replace(/\.\.\./g, " ")
        .replace(/[\-\/\(\)\+\"\[\]\{\}]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
      // Remove cores e termos irrelevantes
      const words = searchQuery.split(' ').filter(w => {
        const lower = w.toLowerCase();
        if (colorWords.includes(lower)) return false;
        if (removeWords.includes(lower)) return false;
        if (w.length <= 1) return false; // remove single chars
        return true;
      });
      // Limitar a no máximo 5 palavras mais relevantes (modelo + capacidade)
      searchQuery = words.slice(0, 5).join(' ').trim();

      // Gerar queries progressivas: se a primeira não retornar, tenta mais curta
      const queryVariants: string[] = [searchQuery];
      if (words.length > 3) {
        queryVariants.push(words.slice(0, 3).join(' '));
      }
      if (words.length > 2) {
        queryVariants.push(words.slice(0, 2).join(' '));
      }

      // Verificar cache
      const cacheKey = searchQuery.toLowerCase();
      const cached = cache.get(cacheKey);
      if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
        return cached.data;
      }

      const results: CompetitorResult[] = [];

      // Tentar queries progressivamente mais curtas até encontrar resultados
      let usedQuery = searchQuery;
      for (const q of queryVariants) {
        try {
          const mlResponse = await axios.get("https://api.mercadolibre.com/sites/MLB/search", {
            params: {
              q: q,
              condition: "new",
              limit: 20,
              sort: "relevance",
            },
            timeout: 15000,
            headers: {
              'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            },
          });

          const mlResults = mlResponse.data?.results || [];
          usedQuery = q;

          if (mlResults.length > 0) {
            for (const item of mlResults) {
              const listingTypeId = item.listing_type_id || "";
              let listingType = "ml classico";
              if (listingTypeId === "gold_pro") listingType = "ml premium";
              else if (listingTypeId === "gold_special") listingType = "ml classico";
              else if (listingTypeId === "gold_premium") listingType = "ml premium";

              results.push({
                platform: "mercadolivre",
                listingType,
                title: item.title || "",
                price: item.price || 0,
                permalink: item.permalink || "",
                sellerId: String(item.seller?.id || ""),
                condition: item.condition || "new",
              });
            }
            console.log(`[CompetitorSearch] Found ${mlResults.length} results with query: "${q}"`);
            break; // Encontrou resultados, para aqui
          } else {
            console.log(`[CompetitorSearch] No results for query: "${q}", trying shorter...`);
          }
        } catch (err: any) {
          console.error(`[CompetitorSearch] ML search error for "${q}":`, err?.message);
          // Se for rate limit (429/403), não tenta mais queries
          if (err?.response?.status === 429 || err?.response?.status === 403) {
            console.warn("[CompetitorSearch] Rate limited, stopping retries");
            break;
          }
        }
      }

      // Agrupar por listing type e calcular estatísticas
      const grouped: Record<string, {
        minPrice: number;
        maxPrice: number;
        avgPrice: number;
        medianPrice: number;
        count: number;
        topResults: Array<{ title: string; price: number; permalink: string; sellerId: string }>;
      }> = {};

      for (const lt of ["ml classico", "ml premium"]) {
        const items = results.filter(r => r.listingType === lt && r.price > 0);
        if (items.length === 0) {
          grouped[lt] = { minPrice: 0, maxPrice: 0, avgPrice: 0, medianPrice: 0, count: 0, topResults: [] };
          continue;
        }
        const prices = items.map(i => i.price).sort((a, b) => a - b);
        const sum = prices.reduce((a, b) => a + b, 0);
        const median = prices.length % 2 === 0
          ? (prices[prices.length / 2 - 1] + prices[prices.length / 2]) / 2
          : prices[Math.floor(prices.length / 2)];

        grouped[lt] = {
          minPrice: prices[0],
          maxPrice: prices[prices.length - 1],
          avgPrice: Math.round(sum / prices.length),
          medianPrice: Math.round(median),
          count: items.length,
          topResults: items.slice(0, 5).map(i => ({
            title: i.title,
            price: i.price,
            permalink: i.permalink,
            sellerId: i.sellerId,
          })),
        };
      }

      const result = { grouped, totalResults: results.length, query: usedQuery };

      // Salvar no cache
      cache.set(cacheKey, { data: result, timestamp: Date.now() });

      return result;
    });
  })(),

  syncBlingCosts: protectedProcedure.mutation(async ({ ctx }) => {
    const ownerId = await getOwnerId();
    const integrations = await getIntegrationsByUserId(ownerId);
    const blingIntegration = integrations.find(i => i.platform === "bling" && i.status === "connected");
    if (!blingIntegration) throw new Error("Nenhuma integra\u00e7\u00e3o com o Bling conectada.");
    const credentials = JSON.parse(blingIntegration.credentials ?? "{}");
    const bling = new BlingService(
      { apiKey: credentials.apiKey ?? credentials.token ?? "", refreshToken: credentials.refreshToken, tokenExpiresAt: credentials.tokenExpiresAt ? Number(credentials.tokenExpiresAt) : undefined },
      blingIntegration.id
    );
    const blingProducts = await bling.getAllProducts();
    // Build maps for matching: by SKU base and by normalized name
    const costBySku = new Map<string, number>();
    const costByName = new Map<string, number>();
    const normalize = (s: string) => s.toLowerCase().trim().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    for (const bp of blingProducts) {
      if (bp.situacao !== "A") continue; // skip inactive/excluded
      if (bp.descricao?.toUpperCase().includes("USADO")) continue; // skip usados
      if (!bp.precoCusto || bp.precoCusto <= 0) continue;
      if ((bp.codigo || "").includes("+")) continue; // skip kits (contain "+" in code)
      // Index by full SKU and SKU base (keep the LOWEST cost)
      const sku = (bp.codigo || "").toLowerCase().trim();
      if (sku) {
        const existing = costBySku.get(sku);
        if (existing === undefined || bp.precoCusto < existing) {
          costBySku.set(sku, bp.precoCusto);
        }
        const skuBase = sku.split(".")[0];
        if (skuBase) {
          const existingBase = costBySku.get(skuBase);
          if (existingBase === undefined || bp.precoCusto < existingBase) {
            costBySku.set(skuBase, bp.precoCusto);
          }
        }
      }
      // Index by normalized name (keep the LOWEST cost)
      if (bp.descricao) {
        const normName = normalize(bp.descricao);
        const existingName = costByName.get(normName);
        if (existingName === undefined || bp.precoCusto < existingName) {
          costByName.set(normName, bp.precoCusto);
        }
      }
    }
    // Update pricing_products with Bling cost
    const pricingProds = await getPricingProducts(ownerId);
    let updated = 0;
    for (const pp of pricingProds) {
      let blingCost: number | undefined = undefined;
      // Strategy 1: match by SKU (split by comma, check each against Bling SKU base map)
      const skuParts = pp.sku.split(",").map(s => s.trim().toLowerCase());
      for (const part of skuParts) {
        const base = part.split(".")[0];
        blingCost = costBySku.get(part) ?? costBySku.get(base);
        if (blingCost !== undefined) break;
      }
      // Strategy 2: match by name (normalized, check if one contains the other)
      if (blingCost === undefined && pp.name) {
        const normPpName = normalize(pp.name);
        // Try exact match first
        blingCost = costByName.get(normPpName);
        // Try contains match
        if (blingCost === undefined) {
          const entries = Array.from(costByName);
          for (let i = 0; i < entries.length; i++) {
            const [blingName, cost] = entries[i];
            if (blingName.includes(normPpName) || normPpName.includes(blingName)) {
              blingCost = cost;
              break;
            }
          }
        }
      }
      if (blingCost !== undefined) {
        await updatePricingProduct(pp.id, ownerId, { blingCostPrice: blingCost.toFixed(2) });
        updated++;
      }
    }
    return { updated, total: pricingProds.length };
  }),
});

// ── Store Info Router ────────────────────────────────────────────────────────────────────────────
const storeInfoRouter = router({
  list: protectedProcedure.query(async ({ ctx }) => {
    return getStoreInfoByUserId(await getOwnerId());
  }),

  create: protectedProcedure.input(z.object({
    platform: z.string(),
    segment: z.string().nullable().optional(),
    freight: z.string().nullable().optional(),
    cpfName: z.string().nullable().optional(),
    accountName: z.string().nullable().optional(),
    server: z.string().nullable().optional(),
    cnpj: z.string().nullable().optional(),
    email: z.string().nullable().optional(),
    observation: z.string().nullable().optional(),
    shippingAddress: z.string().nullable().optional(),
    returnAddress: z.string().nullable().optional(),
    phone: z.string().nullable().optional(),
    password: z.string().nullable().optional(),
    link: z.string().nullable().optional(),
    sortOrder: z.number().optional(),
  })).mutation(async ({ ctx, input }) => {
    const id = await createStoreInfo({ ...input, userId: await getOwnerId() });
    return { id };
  }),

  update: protectedProcedure.input(z.object({
    id: z.number(),
    platform: z.string().optional(),
    segment: z.string().nullable().optional(),
    freight: z.string().nullable().optional(),
    cpfName: z.string().nullable().optional(),
    accountName: z.string().nullable().optional(),
    server: z.string().nullable().optional(),
    cnpj: z.string().nullable().optional(),
    email: z.string().nullable().optional(),
    observation: z.string().nullable().optional(),
    shippingAddress: z.string().nullable().optional(),
    returnAddress: z.string().nullable().optional(),
    phone: z.string().nullable().optional(),
    password: z.string().nullable().optional(),
    link: z.string().nullable().optional(),
    sortOrder: z.number().optional(),
  })).mutation(async ({ ctx, input }) => {
    const { id, ...data } = input;
    await updateStoreInfo(id, await getOwnerId(), data);
    return { success: true };
  }),

  delete: protectedProcedure.input(z.object({ id: z.number() })).mutation(async ({ ctx, input }) => {
    await deleteStoreInfo(input.id, await getOwnerId());
    return { success: true };
  }),

  // Set department for a store — auto-creates or removes pricing account
  setDepartment: protectedProcedure.input(z.object({
    storeId: z.number(),
    departments: z.array(z.enum(["celular", "mala", "eletro", "catalogo"])), // empty = remove all
  })).mutation(async ({ ctx, input }) => {
    const ownerId = await getOwnerId();
    const stores = await getStoreInfoByUserId(ownerId);
    const store = stores.find(s => s.id === input.storeId);
    if (!store) throw new TRPCError({ code: "NOT_FOUND", message: "Loja não encontrada" });

    // Map store platform to pricing platform
    const STORE_TO_PRICING: Record<string, string> = {
      shopee: "shopee", ml: "mercadolivre", amazon: "amazon",
      magalu: "magalu", temu: "temu", tiktok: "tiktok", aliexpress: "aliexpress", shein: "shopee",
    };
    const pricingPlatform = STORE_TO_PRICING[store.platform.toLowerCase()];
    if (!pricingPlatform) throw new TRPCError({ code: "BAD_REQUEST", message: "Plataforma não suportada para tabela de preços" });

    const accountName = (store.accountName || store.platform).trim();
    const allAccounts = await getPricingAccounts(ownerId);

    // Find existing pricing accounts linked to this store
    const linkedAccounts = allAccounts.filter(a => a.storeInfoId === input.storeId);
    const linkedDepts = new Set(linkedAccounts.map(a => a.department));
    const wantedDepts = new Set(input.departments);

    // Remove accounts for departments no longer wanted
    for (const acct of linkedAccounts) {
      if (!wantedDepts.has(acct.department as any)) {
        await deletePricingAccount(acct.id, ownerId);
      }
    }

    // Create accounts for new departments (skip if account with same name+platform+dept already exists)
    for (const dept of input.departments) {
      if (!linkedDepts.has(dept)) {
        // Find existing accounts: exact match OR account name starts with store name
        const nameLC = accountName.toLowerCase();
        const existingManuals = allAccounts.filter(
          a => a.platform === pricingPlatform && a.department === dept && !a.storeInfoId &&
            (a.name.toLowerCase() === nameLC || a.name.toLowerCase().startsWith(nameLC + " "))
        );
        if (existingManuals.length > 0) {
          // Link all matching existing accounts to this store
          for (const acct of existingManuals) {
            await updatePricingAccount(acct.id, ownerId, { storeInfoId: input.storeId });
          }
        } else {
          await createPricingAccount({
            userId: ownerId,
            name: accountName,
            platform: pricingPlatform as any,
            department: dept,
            kitNumber: 1,
            commission: "0.11",
            storeInfoId: input.storeId,
          });
        }
      }
    }

    return { success: true };
  }),
});

// ── App Router ───────────────────────────────────────────────────────────────────────────────────
export const appRouter = router({
  system: systemRouter,
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return { success: true } as const;
    }),
  }),
  integrations: integrationsRouter,
  products: productsRouter,
  sync: syncRouter,
  alerts: alertsRouter,
  settings: settingsRouter,
   listings: listingsRouter,
  audit: auditRouter,
  discrepancies: discrepanciesRouter,
  pricing: pricingRouter,
  storeInfo: storeInfoRouter,
});
export type AppRouter = typeof appRouter;
