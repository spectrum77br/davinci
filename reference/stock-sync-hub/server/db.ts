import { eq, desc, and, sql, like, or, gt } from "drizzle-orm";
import { drizzle } from "drizzle-orm/mysql2";
import {
  InsertUser, users,
  integrations, InsertIntegration,
  products, InsertProduct,
  syncLogs, InsertSyncLog,
  syncQueue, InsertSyncQueue,
  alerts, InsertAlert,
  userSettings, InsertUserSettings,
  pricingAccounts, InsertPricingAccount,
  pricingProducts, InsertPricingProduct,
  pricingOverrides, InsertPricingOverride,
  storeInfo, InsertStoreInfo,
  dismissedAuditSkus,
} from "../drizzle/schema";
import { ENV } from './_core/env';

let _db: ReturnType<typeof drizzle> | null = null;

export async function getDb() {
  if (!_db && process.env.DATABASE_URL) {
    try {
      _db = drizzle(process.env.DATABASE_URL);
    } catch (error) {
      console.warn("[Database] Failed to connect:", error);
      _db = null;
    }
  }
  return _db;
}

/** Reset DB connection (used for reconnection after errors) */
export function resetDbConnection() {
  _db = null;
  console.log("[Database] Connection reset, will reconnect on next query");
}

/** Safe DB operation wrapper - retries once on connection failure */
async function safeDbOp<T>(operation: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await operation();
  } catch (error: any) {
    const msg = error?.message ?? '';
    if (msg.includes('Failed query') || msg.includes('ECONNRESET') || msg.includes('ETIMEDOUT') || msg.includes('Connection lost')) {
      console.warn(`[Database] Connection error, retrying once: ${msg}`);
      resetDbConnection();
      try {
        return await operation();
      } catch (retryError: any) {
        console.warn(`[Database] Retry also failed (non-fatal): ${retryError.message}`);
        return fallback;
      }
    }
    throw error;
  }
}

// ── Users ──────────────────────────────────────────────────────────────────

export async function upsertUser(user: InsertUser): Promise<void> {
  if (!user.openId) throw new Error("User openId is required for upsert");
  const db = await getDb();
  if (!db) { console.warn("[Database] Cannot upsert user: database not available"); return; }

  const values: InsertUser = { openId: user.openId };
  const updateSet: Record<string, unknown> = {};
  const textFields = ["name", "email", "loginMethod"] as const;
  type TextField = (typeof textFields)[number];
  const assignNullable = (field: TextField) => {
    const value = user[field];
    if (value === undefined) return;
    const normalized = value ?? null;
    values[field] = normalized;
    updateSet[field] = normalized;
  };
  textFields.forEach(assignNullable);
  if (user.lastSignedIn !== undefined) { values.lastSignedIn = user.lastSignedIn; updateSet.lastSignedIn = user.lastSignedIn; }
  if (user.role !== undefined) { values.role = user.role; updateSet.role = user.role; }
  else if (user.openId === ENV.ownerOpenId) { values.role = 'admin'; updateSet.role = 'admin'; }
  if (!values.lastSignedIn) values.lastSignedIn = new Date();
  if (Object.keys(updateSet).length === 0) updateSet.lastSignedIn = new Date();
  await db.insert(users).values(values).onDuplicateKeyUpdate({ set: updateSet });
}

export async function getUserByOpenId(openId: string) {
  const db = await getDb();
  if (!db) return undefined;
  const result = await db.select().from(users).where(eq(users.openId, openId)).limit(1);
  return result.length > 0 ? result[0] : undefined;
}

// ── Shared Data: Owner ID Resolution ─────────────────────────────────────────
// All business data is shared: every user sees the owner's data.
// This function resolves the owner's userId from OWNER_OPEN_ID env.
let _cachedOwnerId: number | null = null;

export async function getOwnerId(): Promise<number> {
  if (_cachedOwnerId !== null) return _cachedOwnerId;
  const ownerOpenId = ENV.ownerOpenId;
  if (ownerOpenId) {
    const owner = await getUserByOpenId(ownerOpenId);
    if (owner) {
      _cachedOwnerId = owner.id;
      return owner.id;
    }
  }
  // Fallback: return userId 1 (first user / owner)
  _cachedOwnerId = 1;
  return 1;
}

// ── Integrations ───────────────────────────────────────────────────────────

export async function getIntegrationsByUserId(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(integrations).where(eq(integrations.userId, userId)).orderBy(desc(integrations.createdAt));
}

export async function getIntegrationById(id: number, userId: number) {
  const db = await getDb();
  if (!db) return undefined;
  const result = await db.select().from(integrations).where(and(eq(integrations.id, id), eq(integrations.userId, userId))).limit(1);
  return result[0];
}

export async function createIntegration(data: InsertIntegration) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.insert(integrations).values(data);
  const result = await db.select().from(integrations).where(and(eq(integrations.userId, data.userId), eq(integrations.platform, data.platform))).orderBy(desc(integrations.createdAt)).limit(1);
  return result[0];
}

export async function updateIntegration(id: number, userId: number, data: Partial<InsertIntegration>) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(integrations).set(data).where(and(eq(integrations.id, id), eq(integrations.userId, userId)));
}

export async function deleteIntegration(id: number, userId: number) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.delete(integrations).where(and(eq(integrations.id, id), eq(integrations.userId, userId)));
}

// ── Products ───────────────────────────────────────────────────────────────

export async function getProductsByUserId(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(products).where(eq(products.userId, userId)).orderBy(desc(products.updatedAt));
}

export async function getProductById(id: number, userId: number) {
  const db = await getDb();
  if (!db) return undefined;
  const result = await db.select().from(products).where(and(eq(products.id, id), eq(products.userId, userId))).limit(1);
  return result[0];
}

export async function createProduct(data: InsertProduct) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.insert(products).values(data);
  const result = await db.select().from(products).where(and(eq(products.userId, data.userId), eq(products.sku, data.sku))).orderBy(desc(products.createdAt)).limit(1);
  return result[0];
}

export async function updateProduct(id: number, userId: number, data: Partial<InsertProduct>) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(products).set(data).where(and(eq(products.id, id), eq(products.userId, userId)));
}

export async function deleteProduct(id: number, userId: number) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.delete(products).where(and(eq(products.id, id), eq(products.userId, userId)));
}

// ── Sync Logs ──────────────────────────────────────────────────────────────

export async function getSyncLogs(userId: number, limit = 100) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(syncLogs).where(eq(syncLogs.userId, userId)).orderBy(desc(syncLogs.createdAt)).limit(limit);
}

export async function createSyncLog(data: InsertSyncLog) {
  await safeDbOp(async () => {
    const db = await getDb();
    if (!db) return;
    await db.insert(syncLogs).values(data);
  }, undefined);
}

export async function getSyncStats(userId: number) {
  const db = await getDb();
  if (!db) return { total: 0, success: 0, error: 0, warning: 0 };
  const result = await db.select({
    status: syncLogs.status,
    count: sql<number>`count(*)`,
  }).from(syncLogs).where(eq(syncLogs.userId, userId)).groupBy(syncLogs.status);
  const stats = { total: 0, success: 0, error: 0, warning: 0 };
  for (const row of result) {
    stats.total += Number(row.count);
    if (row.status === 'success') stats.success = Number(row.count);
    if (row.status === 'error') stats.error = Number(row.count);
    if (row.status === 'warning') stats.warning = Number(row.count);
  }
  return stats;
}

// ── Sync Queue ─────────────────────────────────────────────────────────────

export async function addToSyncQueue(data: InsertSyncQueue) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.insert(syncQueue).values(data);
}

export async function getPendingQueueItems(limit = 10) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(syncQueue).where(and(eq(syncQueue.status, 'pending'), sql`${syncQueue.attempts} < ${syncQueue.maxAttempts}`)).orderBy(syncQueue.scheduledAt).limit(limit);
}

export async function updateQueueItem(id: number, data: Partial<InsertSyncQueue>) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(syncQueue).set(data).where(eq(syncQueue.id, id));
}

// ── Alerts ─────────────────────────────────────────────────────────────────

export async function getAlertsByUserId(userId: number, limit = 50) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(alerts).where(eq(alerts.userId, userId)).orderBy(desc(alerts.createdAt)).limit(limit);
}

export async function getLastDailySyncAlert(userId: number) {
  const db = await getDb();
  if (!db) return null;
  const results = await db.select().from(alerts)
    .where(and(
      eq(alerts.userId, userId),
      or(
        like(alerts.title, '%Sync diária%'),
      )
    ))
    .orderBy(desc(alerts.createdAt))
    .limit(1);
  return results[0] ?? null;
}

export async function getUnreadAlertsCount(userId: number) {
  const db = await getDb();
  if (!db) return 0;
  const result = await db.select({ count: sql<number>`count(*)` }).from(alerts).where(and(eq(alerts.userId, userId), eq(alerts.isRead, false)));
  return Number(result[0]?.count ?? 0);
}

export async function createAlert(data: InsertAlert) {
  await safeDbOp(async () => {
    const db = await getDb();
    if (!db) return;
    await db.insert(alerts).values(data);
  }, undefined);
}

export async function markAlertAsRead(id: number, userId: number) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(alerts).set({ isRead: true }).where(and(eq(alerts.id, id), eq(alerts.userId, userId)));
}

export async function markAllAlertsAsRead(userId: number) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(alerts).set({ isRead: true }).where(eq(alerts.userId, userId));
}

// ── User Settings ──────────────────────────────────────────────────────────

export async function getUserSettings(userId: number) {
  const db = await getDb();
  if (!db) return null;
  const result = await db.select().from(userSettings).where(eq(userSettings.userId, userId)).limit(1);
  return result[0] ?? null;
}

export async function upsertUserSettings(userId: number, data: Partial<InsertUserSettings>, source?: string) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  const existing = await getUserSettings(userId);
  
  // ── AUDIT: Log when autoSync changes ──
  if (data.autoSync !== undefined && existing && existing.autoSync !== data.autoSync) {
    const action = data.autoSync ? 'ENABLED' : 'DISABLED';
    const caller = source ?? 'unknown';
    console.log(`[AUDIT] ⚠️ autoSync ${action} for user ${userId} by ${caller} (was: ${existing.autoSync})`);
    
    // Create an alert so the user sees it in the app
    try {
      await db.insert(alerts).values({
        userId,
        type: 'sync_error',
        severity: data.autoSync ? 'info' : 'warning',
        title: `Auto-sync ${data.autoSync ? 'ativado' : 'desativado'}`,
        message: `A sincronização automática foi ${data.autoSync ? 'ativada' : 'desativada'} por: ${caller}`,
        platform: 'bling',
      });
    } catch (e) {
      // Non-fatal: don't block the settings update
    }
  }
  
  if (existing) {
    await db.update(userSettings).set(data).where(eq(userSettings.userId, userId));
  } else {
    await db.insert(userSettings).values({ userId, ...data });
  }
  return getUserSettings(userId);
}

// ── Listings (Anúncios) ────────────────────────────────────────────────────
import { listings, InsertListing, listingRequests, InsertListingRequest, productLinks, InsertProductLink } from "../drizzle/schema";

export async function getListingsByUserId(userId: number, filters?: { platform?: string; status?: string; search?: string }) {
  const db = await getDb();
  if (!db) return [];
  let query = db.select().from(listings).where(eq(listings.userId, userId));
  const results = await query;
  return results.filter(l => {
    if (filters?.platform && l.platform !== filters.platform) return false;
    if (filters?.status && l.status !== filters.status) return false;
    if (filters?.search) {
      const s = filters.search.toLowerCase();
      if (!l.title.toLowerCase().includes(s) && !(l.sku ?? "").toLowerCase().includes(s) && !l.externalId.toLowerCase().includes(s)) return false;
    }
    return true;
  });
}

export async function upsertListing(data: InsertListing) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  const existing = await db.select().from(listings)
    .where(and(eq(listings.userId, data.userId), eq(listings.externalId, data.externalId), eq(listings.platform, data.platform)))
    .limit(1);
  if (existing.length > 0) {
    await db.update(listings).set({ ...data, updatedAt: new Date() })
      .where(and(eq(listings.userId, data.userId), eq(listings.externalId, data.externalId), eq(listings.platform, data.platform)));
    return existing[0];
  } else {
    await db.insert(listings).values(data);
    const inserted = await db.select().from(listings)
      .where(and(eq(listings.userId, data.userId), eq(listings.externalId, data.externalId), eq(listings.platform, data.platform)))
      .limit(1);
    return inserted[0];
  }
}

export async function upsertListingsBulk(dataArray: InsertListing[]) {
  if (dataArray.length === 0) return;
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  const chunkSize = 100;
  for (let i = 0; i < dataArray.length; i += chunkSize) {
    const chunk = dataArray.slice(i, i + chunkSize);
    await db.insert(listings).values(chunk).onDuplicateKeyUpdate({
      set: {
        title: sql`VALUES(title)`,
        stock: sql`VALUES(stock)`,
        status: sql`VALUES(status)`,
        sku: sql`VALUES(sku)`,
        productId: sql`VALUES(productId)`,
        updatedAt: new Date(),
      },
    });
  }
}

export async function deleteListing(id: number, userId: number) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.delete(listings).where(and(eq(listings.id, id), eq(listings.userId, userId)));
}

// ── Listing Requests (Solicitações) ───────────────────────────────────────
export async function getListingRequestsByUserId(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(listingRequests).where(eq(listingRequests.userId, userId));
}

export async function createListingRequest(data: InsertListingRequest) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.insert(listingRequests).values(data);
  const inserted = await db.select().from(listingRequests)
    .where(eq(listingRequests.userId, data.userId))
    .orderBy(listingRequests.id)
    .limit(1);
  // Return the last inserted
  const all = await db.select().from(listingRequests).where(eq(listingRequests.userId, data.userId));
  return all[all.length - 1];
}

export async function updateListingRequestStatus(id: number, userId: number, status: "pending" | "in_progress" | "completed" | "rejected") {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(listingRequests).set({ status }).where(and(eq(listingRequests.id, id), eq(listingRequests.userId, userId)));
}

// ── Product Links (múltiplos anúncios por produto) ──────────────────────────────

export async function getProductLinksByUserId(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(productLinks).where(eq(productLinks.userId, userId));
}

export async function getProductLinksByProductId(productId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(productLinks).where(eq(productLinks.productId, productId));
}

export async function createProductLink(data: InsertProductLink) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  // Check if link already exists (same product + externalId + variationId)
  const existing = await db.select().from(productLinks).where(
    and(
      eq(productLinks.productId, data.productId),
      eq(productLinks.externalId, data.externalId),
      eq(productLinks.platform, data.platform),
    )
  ).limit(1);
  if (existing.length > 0) {
    // Update existing link
    await db.update(productLinks).set(data).where(eq(productLinks.id, existing[0].id));
    return existing[0];
  }
  await db.insert(productLinks).values(data);
  const inserted = await db.select().from(productLinks)
    .where(and(eq(productLinks.productId, data.productId), eq(productLinks.externalId, data.externalId)))
    .limit(1);
  return inserted[0];
}

export async function createProductLinksBulk(dataArray: InsertProductLink[]) {
  if (dataArray.length === 0) return;
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  // Batch insert in chunks of 100 to avoid query size limits
  const chunkSize = 100;
  for (let i = 0; i < dataArray.length; i += chunkSize) {
    const chunk = dataArray.slice(i, i + chunkSize);
    await db.insert(productLinks).values(chunk);
  }
}

export async function updateProductLink(id: number, data: Partial<InsertProductLink>) {
  await safeDbOp(async () => {
    const db = await getDb();
    if (!db) return;
    await db.update(productLinks).set(data).where(eq(productLinks.id, id));
  }, undefined);
}

export async function deleteProductLink(id: number) {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.delete(productLinks).where(eq(productLinks.id, id));
}

// ── Discrepâncias de Estoque ──────────────────────────────────────────────

export async function getStockDiscrepancies(userId: number) {
  const db = await getDb();
  if (!db) return [];
  
  // Buscar todos os produtos ativos com seus links
  const allProducts = await db.select().from(products)
    .where(and(eq(products.userId, userId), eq(products.isActive, true)));
  const allLinks = await db.select().from(productLinks)
    .where(eq(productLinks.userId, userId));
  const allIntegrations = await db.select().from(integrations)
    .where(eq(integrations.userId, userId));
  
  const integMap = new Map(allIntegrations.map(i => [i.id, i.name]));
  const linksByProduct = new Map<number, typeof allLinks>();
  for (const link of allLinks) {
    const existing = linksByProduct.get(link.productId) ?? [];
    existing.push(link);
    linksByProduct.set(link.productId, existing);
  }
  
  const discrepancies: Array<{
    productId: number;
    sku: string;
    name: string;
    blingStock: number;
    links: Array<{
      platform: string;
      integrationName: string;
      externalId: string;
      linkStock: number;
      difference: number;
    }>;
    maxDifference: number;
  }> = [];
  
  for (const product of allProducts) {
    const links = linksByProduct.get(product.id) ?? [];
    if (links.length === 0) continue;
    
    const blingStock = product.blingStock ?? 0;
    const divergentLinks: typeof discrepancies[0]['links'] = [];
    
    for (const link of links) {
      const linkStock = link.stock ?? 0;
      const diff = Math.abs(blingStock - linkStock);
      if (diff > 3) { // Só reporta diferenças > 3 (vendas durante sync são normais)
        divergentLinks.push({
          platform: link.platform,
          integrationName: integMap.get(link.integrationId) ?? 'Desconhecida',
          externalId: link.externalId,
          linkStock,
          difference: diff,
        });
      }
    }
    
    if (divergentLinks.length > 0) {
      const maxDiff = Math.max(...divergentLinks.map(l => l.difference));
      discrepancies.push({
        productId: product.id,
        sku: product.sku,
        name: product.name,
        blingStock,
        links: divergentLinks,
        maxDifference: maxDiff,
      });
    }
  }
  
  // Ordenar por maior discrepância primeiro
  discrepancies.sort((a, b) => b.maxDifference - a.maxDifference);
  
  return discrepancies;
}


// ── Pricing Module ──

export async function getPricingAccounts(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(pricingAccounts).where(eq(pricingAccounts.userId, userId)).orderBy(pricingAccounts.sortOrder);
}

export async function createPricingAccount(data: InsertPricingAccount) {
  const db = await getDb();
  if (!db) return null;
  // Converte '-' para null em campos numéricos (margin/shipping) para evitar erro SQL
  const numericFields = ['margin1', 'shipping1', 'margin2', 'shipping2', 'margin3', 'shipping3', 'margin4', 'shipping4', 'margin5', 'shipping5', 'commission'] as const;
  const sanitized = { ...data };
  for (const field of numericFields) {
    if (field in sanitized && (sanitized as any)[field] === '-') {
      (sanitized as any)[field] = null;
    }
  }
  const [result] = await db.insert(pricingAccounts).values(sanitized);
  return result.insertId;
}

export async function updatePricingAccount(id: number, userId: number, data: Partial<InsertPricingAccount>) {
  const db = await getDb();
  if (!db) return;
  // Converte '-' para null em campos numéricos (margin/shipping) para evitar erro SQL
  const numericFields = ['margin1', 'shipping1', 'margin2', 'shipping2', 'margin3', 'shipping3', 'margin4', 'shipping4', 'margin5', 'shipping5', 'commission'] as const;
  const sanitized = { ...data };
  for (const field of numericFields) {
    if (field in sanitized && (sanitized as any)[field] === '-') {
      (sanitized as any)[field] = null;
    }
  }
  await db.update(pricingAccounts).set(sanitized).where(and(eq(pricingAccounts.id, id), eq(pricingAccounts.userId, userId)));
}

export async function deletePricingAccount(id: number, userId: number) {
  const db = await getDb();
  if (!db) return;
  await db.delete(pricingOverrides).where(and(eq(pricingOverrides.pricingAccountId, id), eq(pricingOverrides.userId, userId)));
  await db.delete(pricingAccounts).where(and(eq(pricingAccounts.id, id), eq(pricingAccounts.userId, userId)));
}

export async function getPricingProducts(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(pricingProducts).where(eq(pricingProducts.userId, userId)).orderBy(pricingProducts.name);
}

export async function createPricingProduct(data: InsertPricingProduct) {
  const db = await getDb();
  if (!db) return null;
  const [result] = await db.insert(pricingProducts).values(data);
  return result.insertId;
}

export async function createPricingProductsBulk(data: InsertPricingProduct[]) {
  const db = await getDb();
  if (!db) return null;
  if (data.length === 0) return null;
  const result = await db.insert(pricingProducts).values(data);
  return result;
}

export async function updatePricingProduct(id: number, userId: number, data: Partial<InsertPricingProduct>) {
  const db = await getDb();
  if (!db) return;
  await db.update(pricingProducts).set(data).where(and(eq(pricingProducts.id, id), eq(pricingProducts.userId, userId)));
}

export async function deletePricingProduct(id: number, userId: number) {
  const db = await getDb();
  if (!db) return;
  await db.delete(pricingOverrides).where(and(eq(pricingOverrides.pricingProductId, id), eq(pricingOverrides.userId, userId)));
  await db.delete(pricingProducts).where(and(eq(pricingProducts.id, id), eq(pricingProducts.userId, userId)));
}

export async function getPricingOverrides(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(pricingOverrides).where(eq(pricingOverrides.userId, userId));
}

export async function upsertPricingOverride(data: InsertPricingOverride) {
  const db = await getDb();
  if (!db) return null;
  // Check if override exists
  const existing = await db.select().from(pricingOverrides)
    .where(and(
      eq(pricingOverrides.pricingProductId, data.pricingProductId),
      eq(pricingOverrides.pricingAccountId, data.pricingAccountId),
      eq(pricingOverrides.userId, data.userId)
    )).limit(1);
  
  if (existing.length > 0) {
    const updateData: Record<string, any> = {};
    if (data.priceOverride !== undefined) updateData.priceOverride = data.priceOverride;
    if (data.cellStatus !== undefined) updateData.cellStatus = data.cellStatus;
    if (Object.keys(updateData).length > 0) {
      await db.update(pricingOverrides).set(updateData).where(eq(pricingOverrides.id, existing[0].id));
    }
    return existing[0].id;
  } else {
    const [result] = await db.insert(pricingOverrides).values(data);
    return result.insertId;
  }
}

export async function deletePricingOverride(productId: number, accountId: number, userId: number) {
  const db = await getDb();
  if (!db) return;
  await db.delete(pricingOverrides).where(and(
    eq(pricingOverrides.pricingProductId, productId),
    eq(pricingOverrides.pricingAccountId, accountId),
    eq(pricingOverrides.userId, userId)
  ));
}

// updateAllPricingProductMargins removed - no longer needed with new schema

/**
 * Encontra os product_links correspondentes a um pricing_product em uma pricing_account.
 * 
 * Estratégia simplificada:
 * 1. Usar o campo `sku` do pricing_product que contém SKU bases separados por vírgula
 *    (ex: "x043,x044,x045,x046" para Redmi 15c 8.128)
 * 2. Buscar product_links da integração ESPECÍFICA da conta
 * 3. Filtrar por SKU base do product vinculado ao product_link
 * 4. Filtrar por listingType se for ML (classico → gold_special, premium → gold_pro)
 * 
 * Se o campo sku estiver vazio, o push não é possível (produto sem mapeamento).
 */
export async function findProductLinksForPricingPush(
  userId: number,
  pricingProductId: number,
  pricingAccountId: number,
): Promise<Array<{ externalId: string; variationId: string | null; platform: string; productSku: string }>> {
  const db = await getDb();
  if (!db) return [];

  // 1. Buscar pricing_product e pricing_account
  const [pProduct] = await db.select().from(pricingProducts)
    .where(and(eq(pricingProducts.id, pricingProductId), eq(pricingProducts.userId, userId)));
  const [pAccount] = await db.select().from(pricingAccounts)
    .where(and(eq(pricingAccounts.id, pricingAccountId), eq(pricingAccounts.userId, userId)));
  if (!pProduct || !pAccount) return [];
  if (!pAccount.integrationId) return [];

  // 2. Extrair SKUs do campo sku do pricing_product
  const skuField = (pProduct.sku || "").trim();
  if (!skuField) return []; // Sem mapeamento de SKU → push não possível
  const skuList = skuField.split(",").map(s => s.trim()).filter(Boolean);
  if (skuList.length === 0) return [];
  // Set de SKUs completos e set de SKU bases
  const skuFullSet = new Set(skuList);
  const skuBaseSet = new Set(skuList.map(s => s.split(".")[0]));
  // Usar o department do pricing_product (não detectar por pontos!)
  // Kit 6 de mala usa SKU base (b005) mas é department=mala,
  // então precisa de match exato para não pegar b005.12.18
  const isMalaDepartment = pProduct.department === "mala";
  // Catálogo ML: match exato pelo SKU completo (inclui o +, ex: x015.ra+a001.ra)
  const isCatalogoDepartment = pProduct.department === "catalogo";

  // 3. Mapear listingType da pricing_account para o listingType do product_links
  // ML: gold_special = Clássico, gold_pro = Premium (confirmado na doc oficial)
  const mlListingTypeMap: Record<string, string> = {
    "ml_premium": "gold_pro",
    "ml premium": "gold_pro",
    "ml_classico": "gold_special",
    "ml classico": "gold_special",
  };
  const targetListingType = pAccount.platform === "mercadolivre"
    ? mlListingTypeMap[pAccount.listingType?.toLowerCase() || ""] || null
    : null;

  // 4. Buscar TODOS os product_links da integração específica da conta
  const allLinks = await db.select({
    externalId: productLinks.externalId,
    variationId: productLinks.variationId,
    platform: productLinks.platform,
    productId: productLinks.productId,
    listingType: productLinks.listingType,
  }).from(productLinks)
    .where(and(
      eq(productLinks.userId, userId),
      eq(productLinks.integrationId, pAccount.integrationId),
    ));

  // 5. Filtrar por listingType se for ML
  const filteredLinks = targetListingType
    ? allLinks.filter(l => l.listingType === targetListingType)
    : allLinks;

  // 6. Buscar TODOS os products para mapear productId → SKU
  const allProducts = await db.select({ id: products.id, sku: products.sku })
    .from(products)
    .where(eq(products.userId, userId));
  const productMap = new Map(allProducts.map(p => [p.id, p.sku]));

  // 7. Filtrar links cujo SKU corresponde ao pricing_product
  // Mala: match por SKU completo (b005.12.18) — cada tamanho é um anúncio diferente
  // Celular: match por SKU base (dg060) — variações são do mesmo anúncio
  const result: Array<{ externalId: string; variationId: string | null; platform: string; productSku: string }> = [];
  const seenExternalIds = new Set<string>();

  for (const link of filteredLinks) {
    const sku = productMap.get(link.productId);
    if (!sku) continue;
    if (isCatalogoDepartment) {
      // Catálogo: SKU simples (ex: x015.ra) → pega só anúncios SEM "+" no SKU
      if (sku.includes("+")) continue; // Ignorar anúncios de kit
      // Match exato pelo SKU completo (ex: x015.ra)
      if (!skuFullSet.has(sku)) continue;
    } else {
      // Extrair SKU principal (antes do +)
      const mainSku = sku.split("+")[0];
      if (isMalaDepartment) {
        // Mala: comparar SKU completo (ex: b005.12.18)
        if (!skuFullSet.has(mainSku)) continue;
      } else {
        // Celular: comparar SKU base (ex: dg060)
        // Para ML: pega só anúncios COM "+" (kits) — individuais sem "+" são do Catálogo ML
        // Para Shopee/Amazon/TikTok/Temu: pega qualquer anúncio (não existe catálogo nessas plataformas)
        if (link.platform === "mercadolivre" && !sku.includes("+")) continue;
        const skuBase = mainSku.split(".")[0];
        if (!skuBaseSet.has(skuBase)) continue;
      }
    }

    // Para ML e Amazon, agrupar por externalId (o preço é por anúncio, não por variação)
    // Para Shopee e TikTok, cada variação/SKU precisa ser atualizado separadamente
    const key = (link.platform === "shopee" || link.platform === "tiktok")
      ? `${link.externalId}:${link.variationId || "0"}`
      : link.externalId;
    if (seenExternalIds.has(key)) continue;
    seenExternalIds.add(key);

    result.push({
      externalId: link.externalId,
      variationId: link.variationId,
      platform: link.platform,
      productSku: sku,
    });
  }

  return result;
}

// ── Store Info (Loja) ─────────────────────────────────────────────────────

export async function getStoreInfoByUserId(userId: number) {
  const db = await getDb();
  if (!db) return [];
  return db.select().from(storeInfo).where(eq(storeInfo.userId, userId)).orderBy(storeInfo.platform, storeInfo.sortOrder);
}

export async function createStoreInfo(data: InsertStoreInfo) {
  const db = await getDb();
  if (!db) return null;
  const [result] = await db.insert(storeInfo).values(data);
  return result.insertId;
}

export async function updateStoreInfo(id: number, userId: number, data: Partial<InsertStoreInfo>) {
  const db = await getDb();
  if (!db) return;
  await db.update(storeInfo).set(data).where(and(eq(storeInfo.id, id), eq(storeInfo.userId, userId)));
}

export async function deleteStoreInfo(id: number, userId: number) {
  const db = await getDb();
  if (!db) return;
  await db.delete(storeInfo).where(and(eq(storeInfo.id, id), eq(storeInfo.userId, userId)));
}

// ── SKU Audit ────────────────────────────────────────────────────────────────
// Critério: produtos do Bling com estoque > 0 que:
//   - NÃO têm anúncio vinculado em nenhuma conta (sem productLinks) OU
//   - NÃO estão na tabela de preços
export async function getSkuAudit(userId: number) {
  const db = await getDb();
  if (!db) return [];

  // 1. Get all products from Bling with stock > 0 and active
  const allProducts = await db.select({
    id: products.id,
    sku: products.sku,
    name: products.name,
    blingStock: products.blingStock,
  }).from(products).where(
    and(
      eq(products.userId, userId),
      gt(products.blingStock, 0),
      eq(products.isActive, true),
    )
  );

  if (allProducts.length === 0) return [];

  // 2. Get all SKUs from pricing_products (comma-separated, need to split)
  const allPricingProducts = await db.select({
    sku: pricingProducts.sku,
  }).from(pricingProducts).where(eq(pricingProducts.userId, userId));

  const pricingSkuSet = new Set<string>();
  for (const pp of allPricingProducts) {
    if (pp.sku) {
      const skus = pp.sku.split(",").map(s => s.trim().toLowerCase()).filter(Boolean);
      for (const s of skus) {
        pricingSkuSet.add(s);
      }
    }
  }

  // 3. Get dismissed SKUs
  const dismissedRows = await db.select({ sku: dismissedAuditSkus.sku })
    .from(dismissedAuditSkus)
    .where(eq(dismissedAuditSkus.userId, userId));
  const dismissedSet = new Set(dismissedRows.map(r => r.sku.trim().toLowerCase()));

  // 4. Get all productLinks to know which products have marketplace links
  const allLinks = await db.select({
    productId: productLinks.productId,
    integrationId: productLinks.integrationId,
    platform: productLinks.platform,
  }).from(productLinks).where(eq(productLinks.userId, userId));

  // Build productId → Set of integrationIds
  const productIdToIntegrationIds = new Map<number, Set<number>>();
  for (const link of allLinks) {
    const existing = productIdToIntegrationIds.get(link.productId) ?? new Set();
    existing.add(link.integrationId);
    productIdToIntegrationIds.set(link.productId, existing);
  }

  // 5. Get all integrations to map id → name
  const allIntegrations = await db.select({
    id: integrations.id,
    name: integrations.name,
  }).from(integrations).where(eq(integrations.userId, userId));
  const integMap = new Map(allIntegrations.map(i => [i.id, i.name]));

  // 6. For each product with stock > 0, check if it has links AND is in pricing table
  const missingSkus: Array<{
    id: number;
    platform: string;
    sku: string;
    title: string;
    externalId: string;
    dismissed: boolean;
    accounts: string[];
    accountCount: number;
    inBling: boolean;
    inPricing: boolean;
    hasLinks: boolean;
    stock: number | null;
    issues: string[]; // what's missing: "Sem anúncio" / "Fora da tabela de preços"
  }> = [];

  for (const product of allProducts) {
    const skuLower = product.sku.trim().toLowerCase();
    if (!skuLower) continue;
    const baseSku = skuLower.split(".")[0];

    // Check if in pricing table
    const inPricing = pricingSkuSet.has(skuLower) || pricingSkuSet.has(baseSku);

    // Check if has marketplace links
    const integrationIds = productIdToIntegrationIds.get(product.id);
    const hasLinks = !!integrationIds && integrationIds.size > 0;

    // If both are OK, skip — product is fully resolved
    if (inPricing && hasLinks) continue;

    // Build issues list
    const issues: string[] = [];
    if (!hasLinks) issues.push("Sem anúncio");
    if (!inPricing) issues.push("Fora da tabela de preços");

    // Get linked account names
    const accountNames: string[] = [];
    if (integrationIds) {
      for (const intId of Array.from(integrationIds)) {
        const name = integMap.get(intId);
        if (name) accountNames.push(name);
      }
    }

    const isDismissed = dismissedSet.has(skuLower) || dismissedSet.has(baseSku);

    missingSkus.push({
      id: product.id,
      platform: "bling",
      sku: product.sku,
      title: product.name,
      externalId: product.sku,
      dismissed: isDismissed,
      accounts: accountNames,
      accountCount: accountNames.length,
      inBling: true,
      inPricing,
      hasLinks,
      stock: product.blingStock ?? 0,
      issues,
    });
  }

  return missingSkus;
}

// ── Dismiss/Undismiss Audit SKUs ─────────────────────────────────────────────
export async function dismissAuditSku(userId: number, sku: string) {
  const db = await getDb();
  if (!db) return false;
  const skuLower = sku.trim().toLowerCase();
  // Check if already dismissed
  const existing = await db.select({ id: dismissedAuditSkus.id })
    .from(dismissedAuditSkus)
    .where(and(eq(dismissedAuditSkus.userId, userId), eq(dismissedAuditSkus.sku, skuLower)))
    .limit(1);
  if (existing.length > 0) return true;
  await db.insert(dismissedAuditSkus).values({ userId, sku: skuLower });
  return true;
}

export async function undismissAuditSku(userId: number, sku: string) {
  const db = await getDb();
  if (!db) return false;
  const skuLower = sku.trim().toLowerCase();
  await db.delete(dismissedAuditSkus)
    .where(and(eq(dismissedAuditSkus.userId, userId), eq(dismissedAuditSkus.sku, skuLower)));
  return true;
}

// ── Delete Product by Bling ID (for webhook product deletion) ──────────────
export async function deleteProductByBlingId(blingId: string): Promise<{
  deleted: boolean;
  sku?: string;
  name?: string;
  userId?: number;
  deletedLinks: number;
  deletedPricingProducts: number;
  deletedDismissals: number;
}> {
  const db = await getDb();
  if (!db) return { deleted: false, deletedLinks: 0, deletedPricingProducts: 0, deletedDismissals: 0 };

  // Find the product by blingId
  const matchingProducts = await db.select().from(products)
    .where(eq(products.blingId, blingId));

  if (matchingProducts.length === 0) {
    return { deleted: false, deletedLinks: 0, deletedPricingProducts: 0, deletedDismissals: 0 };
  }

  let totalDeletedLinks = 0;
  let totalDeletedPricingProducts = 0;
  let totalDeletedDismissals = 0;
  let lastSku = "";
  let lastName = "";
  let lastUserId = 0;

  for (const product of matchingProducts) {
    lastSku = product.sku;
    lastName = product.name;
    lastUserId = product.userId;

    // 1. Delete product_links
    const links = await db.select({ id: productLinks.id }).from(productLinks)
      .where(eq(productLinks.productId, product.id));
    if (links.length > 0) {
      await db.delete(productLinks).where(eq(productLinks.productId, product.id));
      totalDeletedLinks += links.length;
    }

    // 2. Delete pricing_products + their overrides
    const pricingProds = await db.select({ id: pricingProducts.id }).from(pricingProducts)
      .where(and(eq(pricingProducts.userId, product.userId), eq(pricingProducts.sku, product.sku)));
    for (const pp of pricingProds) {
      await db.delete(pricingOverrides).where(eq(pricingOverrides.pricingProductId, pp.id));
      await db.delete(pricingProducts).where(eq(pricingProducts.id, pp.id));
      totalDeletedPricingProducts++;
    }

    // 3. Delete dismissed_audit_skus
    const dismissed = await db.select({ id: dismissedAuditSkus.id }).from(dismissedAuditSkus)
      .where(and(eq(dismissedAuditSkus.userId, product.userId), eq(dismissedAuditSkus.sku, product.sku.toLowerCase().trim())));
    if (dismissed.length > 0) {
      await db.delete(dismissedAuditSkus)
        .where(and(eq(dismissedAuditSkus.userId, product.userId), eq(dismissedAuditSkus.sku, product.sku.toLowerCase().trim())));
      totalDeletedDismissals += dismissed.length;
    }

    // 4. Delete the product itself
    await db.delete(products).where(eq(products.id, product.id));
  }

  return {
    deleted: true,
    sku: lastSku,
    name: lastName,
    userId: lastUserId,
    deletedLinks: totalDeletedLinks,
    deletedPricingProducts: totalDeletedPricingProducts,
    deletedDismissals: totalDeletedDismissals,
  };
}

// ── Update Product by Bling ID (for webhook product update) ──────────────
export async function updateProductByBlingId(blingId: string, data: { name?: string; sku?: string; stock?: number; isActive?: boolean }): Promise<{
  updated: boolean;
  sku?: string;
  name?: string;
  userId?: number;
}> {
  const db = await getDb();
  if (!db) return { updated: false };

  const matchingProducts = await db.select().from(products)
    .where(eq(products.blingId, blingId));

  if (matchingProducts.length === 0) {
    return { updated: false };
  }

  for (const product of matchingProducts) {
    const updateFields: Record<string, any> = {};
    if (data.name !== undefined) updateFields.name = data.name;
    if (data.sku !== undefined) updateFields.sku = data.sku;
    if (data.stock !== undefined) updateFields.blingStock = data.stock;
    if (data.isActive !== undefined) updateFields.isActive = data.isActive;
    updateFields.updatedAt = new Date();

    if (Object.keys(updateFields).length > 0) {
      await db.update(products).set(updateFields).where(eq(products.id, product.id));
    }
  }

  return {
    updated: true,
    sku: matchingProducts[0].sku,
    name: matchingProducts[0].name,
    userId: matchingProducts[0].userId,
  };
}
