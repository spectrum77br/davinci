import {
  int,
  mysqlEnum,
  mysqlTable,
  text,
  timestamp,
  varchar,
  boolean,
  json,
  bigint,
  decimal,
} from "drizzle-orm/mysql-core";

export const users = mysqlTable("users", {
  id: int("id").autoincrement().primaryKey(),
  openId: varchar("openId", { length: 64 }).notNull().unique(),
  name: text("name"),
  email: varchar("email", { length: 320 }),
  loginMethod: varchar("loginMethod", { length: 64 }),
  role: mysqlEnum("role", ["user", "admin"]).default("user").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  lastSignedIn: timestamp("lastSignedIn").defaultNow().notNull(),
});

export type User = typeof users.$inferSelect;
export type InsertUser = typeof users.$inferInsert;

// Integrações com marketplaces (armazena credenciais criptografadas)
export const integrations = mysqlTable("integrations", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  platform: mysqlEnum("platform", ["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]).notNull(),
  name: varchar("name", { length: 128 }).notNull(),
  isActive: boolean("isActive").default(true).notNull(),
  credentials: text("credentials").notNull(), // JSON criptografado com as credenciais
  lastSyncAt: timestamp("lastSyncAt"),
  status: mysqlEnum("status", ["connected", "disconnected", "error"]).default("disconnected").notNull(),
  errorMessage: text("errorMessage"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type Integration = typeof integrations.$inferSelect;
export type InsertIntegration = typeof integrations.$inferInsert;

// Produtos mapeados entre plataformas
export const products = mysqlTable("products", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  sku: varchar("sku", { length: 128 }).notNull(),
  name: varchar("name", { length: 512 }).notNull(),
  blingId: varchar("blingId", { length: 128 }),
  blingStock: int("blingStock").default(0),
  shopeeId: varchar("shopeeId", { length: 128 }),
  shopeeModelId: varchar("shopeeModelId", { length: 128 }),
  shopeeStock: int("shopeeStock").default(0),
  amazonId: varchar("amazonId", { length: 128 }),
  amazonStock: int("amazonStock").default(0),
  mercadolivreId: varchar("mercadolivreId", { length: 128 }),
  mercadolivreVariationId: varchar("mercadolivreVariationId", { length: 128 }),
  mercadolivreStock: int("mercadolivreStock").default(0),
  tiktokId: varchar("tiktokId", { length: 128 }),
  tiktokSkuId: varchar("tiktokSkuId", { length: 128 }),
  tiktokStock: int("tiktokStock").default(0),
  temuId: varchar("temuId", { length: 128 }),
  temuSkuId: varchar("temuSkuId", { length: 128 }),
  temuStock: int("temuStock").default(0),
  // IDs das integrações vinculadas (para suporte a múltiplas contas por marketplace)
  shopeeIntegrationId: int("shopeeIntegrationId"),
  amazonIntegrationId: int("amazonIntegrationId"),
  mercadolivreIntegrationId: int("mercadolivreIntegrationId"),
  tiktokIntegrationId: int("tiktokIntegrationId"),
  temuIntegrationId: int("temuIntegrationId"),
  lowStockThreshold: int("lowStockThreshold").default(5),
  isActive: boolean("isActive").default(true).notNull(),
  lastSyncAt: timestamp("lastSyncAt"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type Product = typeof products.$inferSelect;
export type InsertProduct = typeof products.$inferInsert;

// Fila de sincronização
export const syncQueue = mysqlTable("sync_queue", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  productId: int("productId"),
  integrationId: int("integrationId"),
  action: mysqlEnum("action", ["sync_stock", "full_sync", "test_connection"]).notNull(),
  platform: mysqlEnum("platform", ["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu", "all"]).notNull(),
  status: mysqlEnum("status", ["pending", "processing", "completed", "failed"]).default("pending").notNull(),
  payload: text("payload"), // JSON com dados da tarefa
  result: text("result"), // JSON com resultado
  errorMessage: text("errorMessage"),
  attempts: int("attempts").default(0).notNull(),
  maxAttempts: int("maxAttempts").default(3).notNull(),
  scheduledAt: timestamp("scheduledAt").defaultNow().notNull(),
  startedAt: timestamp("startedAt"),
  completedAt: timestamp("completedAt"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});

export type SyncQueue = typeof syncQueue.$inferSelect;
export type InsertSyncQueue = typeof syncQueue.$inferInsert;

// Logs de sincronização
export const syncLogs = mysqlTable("sync_logs", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  productId: int("productId"),
  integrationId: int("integrationId"),
  platform: mysqlEnum("platform", ["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]).notNull(),
  action: varchar("action", { length: 128 }).notNull(),
  status: mysqlEnum("status", ["success", "error", "warning", "skipped"]).notNull(),
  message: text("message").notNull(),
  details: text("details"), // JSON com detalhes adicionais
  stockBefore: int("stockBefore"),
  stockAfter: int("stockAfter"),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});

export type SyncLog = typeof syncLogs.$inferSelect;
export type InsertSyncLog = typeof syncLogs.$inferInsert;

// Alertas e notificações
export const alerts = mysqlTable("alerts", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  type: mysqlEnum("type", ["sync_error", "low_stock", "connection_lost", "stock_discrepancy", "sync_success", "stock_restock"]).notNull(),
  severity: mysqlEnum("severity", ["info", "warning", "error", "critical"]).notNull(),
  title: varchar("title", { length: 256 }).notNull(),
  message: text("message").notNull(),
  platform: mysqlEnum("platform", ["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]).default("bling"),
  productId: int("productId"),
  isRead: boolean("isRead").default(false).notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});

export type Alert = typeof alerts.$inferSelect;
export type InsertAlert = typeof alerts.$inferInsert;

// Configurações do usuário
export const userSettings = mysqlTable("user_settings", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull().unique(),
  syncIntervalMinutes: int("syncIntervalMinutes").default(15).notNull(),
  lowStockThreshold: int("lowStockThreshold").default(5).notNull(),
  emailNotifications: boolean("emailNotifications").default(true).notNull(),
  inAppNotifications: boolean("inAppNotifications").default(true).notNull(),
  notifyOnSyncError: boolean("notifyOnSyncError").default(true).notNull(),
  notifyOnLowStock: boolean("notifyOnLowStock").default(true).notNull(),
  notifyOnDiscrepancy: boolean("notifyOnDiscrepancy").default(true).notNull(),
  autoSync: boolean("autoSync").default(true).notNull(),
  dailySyncTime: varchar("dailySyncTime", { length: 5 }).default("00:00"),  // HH:mm format, horário fixo da sync diária (Brasília)
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type UserSettings = typeof userSettings.$inferSelect;
export type InsertUserSettings = typeof userSettings.$inferInsert;

// Anúncios importados dos marketplaces
export const listings = mysqlTable("listings", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  platform: mysqlEnum("platform", ["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]).notNull(),
  externalId: varchar("externalId", { length: 256 }).notNull(), // ID do anúncio na plataforma
  sku: varchar("sku", { length: 128 }),
  title: varchar("title", { length: 512 }).notNull(),
  description: text("description"),
  price: bigint("price", { mode: "number" }), // em centavos
  stock: int("stock").default(0),
  status: mysqlEnum("status", ["active", "paused", "closed", "under_review", "inactive"]).default("active").notNull(),
  category: varchar("category", { length: 256 }),
  thumbnailUrl: text("thumbnailUrl"),
  productId: int("productId"), // FK para products (se mapeado)
  rawData: text("rawData"), // JSON com dados brutos da plataforma
  importedAt: timestamp("importedAt").defaultNow().notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type Listing = typeof listings.$inferSelect;
export type InsertListing = typeof listings.$inferInsert;

// Solicitações de anúncio
export const listingRequests = mysqlTable("listing_requests", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  platform: mysqlEnum("platform", ["bling", "shopee", "amazon", "mercadolivre", "tiktok", "temu"]).notNull(),
  sku: varchar("sku", { length: 128 }),
  productName: varchar("productName", { length: 512 }).notNull(),
  description: text("description"),
  requestedPrice: bigint("requestedPrice", { mode: "number" }),
  category: varchar("category", { length: 256 }),
  notes: text("notes"),
  status: mysqlEnum("status", ["pending", "in_progress", "completed", "rejected"]).default("pending").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type ListingRequest = typeof listingRequests.$inferSelect;
export type InsertListingRequest = typeof listingRequests.$inferInsert;

// Vínculos produto ↔ anúncios marketplace (suporta múltiplos anúncios por SKU)
export const productLinks = mysqlTable("product_links", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  productId: int("productId").notNull(),
  platform: mysqlEnum("platform", ["shopee", "amazon", "mercadolivre", "tiktok", "temu", "aliexpress"]).notNull(),
  integrationId: int("integrationId").notNull(),
  externalId: varchar("externalId", { length: 128 }).notNull(),
  variationId: varchar("variationId", { length: 128 }),
  stock: int("stock").default(0),
  listingType: varchar("listingType", { length: 64 }), // ex: 'gold_special', 'gold_pro', 'free'
  lastSyncAt: timestamp("lastSyncAt"),
  suspendedAt: timestamp("suspendedAt"), // set when marketplace returns "status abnormal" / banned / deleted
  suspendedReason: varchar("suspendedReason", { length: 255 }), // reason for suspension
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type ProductLink = typeof productLinks.$inferSelect;
export type InsertProductLink = typeof productLinks.$inferInsert;

// ── Módulo Tabela de Preços ──

// Contas de pricing (cada conta de marketplace com 5 pares margem/frete por tipo de produto)
export const pricingAccounts = mysqlTable("pricing_accounts", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  name: varchar("name", { length: 128 }).notNull(), // ex: "inova", "kfa", "marquezini"
  platform: mysqlEnum("platform", ["mercadolivre", "shopee", "temu", "amazon", "aliexpress", "tiktok", "magalu"]).notNull(),
  listingType: varchar("listingType", { length: 64 }), // "ml classico", "ml premium", "shopee", etc.
  department: mysqlEnum("department", ["celular", "mala", "eletro", "catalogo"]).notNull().default("celular"),
  kitNumber: int("kitNumber").notNull().default(1), // 1-4 (qual kit de custo usar)
  commission: decimal("commission", { precision: 6, scale: 4 }).notNull().default("0.11"), // taxa da plataforma (ex: 0.14 = 14%)
  transport: varchar("transport", { length: 64 }), // "correios" ou "agencia"
  // 5 pares de margem/frete (tipo 1-5)
  margin1: decimal("margin1", { precision: 6, scale: 4 }), // margem tipo 1
  shipping1: decimal("shipping1", { precision: 8, scale: 2 }), // frete tipo 1
  margin2: decimal("margin2", { precision: 6, scale: 4 }), // margem tipo 2
  shipping2: decimal("shipping2", { precision: 8, scale: 2 }), // frete tipo 2
  margin3: decimal("margin3", { precision: 6, scale: 4 }), // margem tipo 3
  shipping3: decimal("shipping3", { precision: 8, scale: 2 }), // frete tipo 3
  margin4: decimal("margin4", { precision: 6, scale: 4 }), // margem tipo 4
  shipping4: decimal("shipping4", { precision: 8, scale: 2 }), // frete tipo 4
  margin5: decimal("margin5", { precision: 6, scale: 4 }), // margem tipo 5
  shipping5: decimal("shipping5", { precision: 8, scale: 2 }), // frete tipo 5
  // Dados cadastrais da conta
  server: varchar("server", { length: 64 }),
  email: varchar("email", { length: 256 }),
  password: varchar("password", { length: 256 }),
  phone: varchar("phone", { length: 64 }),
  shippingAddress: text("shippingAddress"),
  returnAddress: text("returnAddress"),
  observation: text("observation"), // Observação 1 por loja
  observation2: text("observation2"), // Observação 2 por loja
  observation3: text("observation3"), // Observação 3 por loja
  storeInfoId: int("storeInfoId"), // FK para store_info (vínculo com loja)
  integrationId: int("integrationId"), // FK para integrations (para atualizar preço via API)
  sortOrder: int("sortOrder").default(0),
  isActive: boolean("isActive").default(true).notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type PricingAccount = typeof pricingAccounts.$inferSelect;
export type InsertPricingAccount = typeof pricingAccounts.$inferInsert;

// Produtos de pricing (cada produto com custos por kit e tipo)
export const pricingProducts = mysqlTable("pricing_products", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  productId: int("productId"), // FK para products (opcional, para vincular ao estoque)
  sku: varchar("sku", { length: 2048 }).notNull(),
  name: varchar("name", { length: 512 }).notNull(),
  department: mysqlEnum("department", ["celular", "mala", "eletro", "catalogo"]).notNull().default("celular"),
  productType: int("productType").notNull().default(2), // 1-5 (tipo do produto, determina qual margem/frete usar)
  // Custo do Bling (precoCusto da API, somente produtos ativos)
  blingCostPrice: decimal("blingCostPrice", { precision: 10, scale: 2 }), // preço de custo do Bling
  // Custos por kit (celular tem 4 kits, mala usa só kit1)
  costKit1: decimal("costKit1", { precision: 10, scale: 2 }).notNull().default("0"), // custo kit 1
  costKit2: decimal("costKit2", { precision: 10, scale: 2 }), // custo kit 2 (celular)
  costKit3: decimal("costKit3", { precision: 10, scale: 2 }), // custo kit 3 (celular)
  costKit4: decimal("costKit4", { precision: 10, scale: 2 }), // custo kit 4 (celular)
  // Campos extras para mala
  description: varchar("description", { length: 256 }), // dimensões/peso (ex: "30x20x20 2kg")
  model: varchar("model", { length: 128 }), // modelo (ex: "M1 lisa", "M2 textura")
  ean: varchar("ean", { length: 64 }), // código EAN/GTIN do produto
  isActive: boolean("isActive").default(true).notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type PricingProduct = typeof pricingProducts.$inferSelect;
export type InsertPricingProduct = typeof pricingProducts.$inferInsert;

// Overrides de preço por produto × conta (preço fixo manual)
export const pricingOverrides = mysqlTable("pricing_overrides", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  pricingProductId: int("pricingProductId").notNull(), // FK para pricing_products
  pricingAccountId: int("pricingAccountId").notNull(), // FK para pricing_accounts
  priceOverride: decimal("priceOverride", { precision: 10, scale: 2 }), // preço fixo manual (ignora fórmula)
  cellStatus: varchar("cellStatus", { length: 20 }), // NA (Não Anunciar), SV (Sem Vínculo), error, no_link, null (normal)
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type PricingOverride = typeof pricingOverrides.$inferSelect;
export type InsertPricingOverride = typeof pricingOverrides.$inferInsert;

// Informações das lojas (emails, senhas, servidor, telefone, etc.)
export const storeInfo = mysqlTable("store_info", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  platform: varchar("platform", { length: 64 }).notNull(), // aliexpress, amazon, magalu, ml, shopee, temu, tiktok, shein
  segment: varchar("segment", { length: 128 }), // segmento (afiliado, diversos, mala)
  freight: varchar("freight", { length: 128 }), // frete (correios, dba pi, dba sp)
  cpfName: varchar("cpfName", { length: 128 }), // nome da pessoa (cpf)
  accountName: varchar("accountName", { length: 128 }), // nome da conta
  server: varchar("server", { length: 64 }), // número do servidor
  cnpj: varchar("cnpj", { length: 32 }), // CNPJ
  email: varchar("email", { length: 256 }), // email/login
  observation: text("observation"), // observações
  shippingAddress: text("shippingAddress"), // endereço de envio
  returnAddress: text("returnAddress"), // endereço devolução
  phone: varchar("phone", { length: 64 }), // telefone
  password: varchar("password", { length: 256 }), // senha
  link: text("link"), // link da plataforma
  sortOrder: int("sortOrder").default(0),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type StoreInfo = typeof storeInfo.$inferSelect;
export type InsertStoreInfo = typeof storeInfo.$inferInsert;

// ── Dismissed Audit SKUs ──────────────────────────────────────────────────────
export const dismissedAuditSkus = mysqlTable("dismissed_audit_skus", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId").notNull(),
  sku: varchar("sku", { length: 128 }).notNull(),
  dismissedAt: timestamp("dismissedAt").defaultNow().notNull(),
});

export type DismissedAuditSku = typeof dismissedAuditSkus.$inferSelect;
export type InsertDismissedAuditSku = typeof dismissedAuditSkus.$inferInsert;
