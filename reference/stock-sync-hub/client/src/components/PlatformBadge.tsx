import { cn } from "@/lib/utils";

type Platform = "bling" | "shopee" | "amazon" | "mercadolivre" | "tiktok" | "temu";
type Status = "connected" | "disconnected" | "error";

const platformConfig: Record<Platform, { label: string; color: string; bg: string }> = {
  bling: { label: "Bling", color: "text-emerald-700", bg: "bg-emerald-50 border-emerald-200" },
  shopee: { label: "Shopee", color: "text-orange-700", bg: "bg-orange-50 border-orange-200" },
  amazon: { label: "Amazon", color: "text-yellow-700", bg: "bg-yellow-50 border-yellow-200" },
  mercadolivre: { label: "Mercado Livre", color: "text-blue-700", bg: "bg-blue-50 border-blue-200" },
  tiktok: { label: "TikTok Shop", color: "text-pink-700", bg: "bg-pink-50 border-pink-200" },
  temu: { label: "Temu", color: "text-purple-700", bg: "bg-purple-50 border-purple-200" },
};

const statusConfig: Record<Status, { label: string; color: string; dot: string }> = {
  connected: { label: "Conectado", color: "text-emerald-600", dot: "bg-emerald-500" },
  disconnected: { label: "Desconectado", color: "text-gray-500", dot: "bg-gray-400" },
  error: { label: "Erro", color: "text-red-600", dot: "bg-red-500" },
};

export function PlatformBadge({ platform, className }: { platform: Platform; className?: string }) {
  const config = platformConfig[platform] ?? { label: platform, color: "text-gray-600", bg: "bg-gray-50 border-gray-200" };
  return (
    <span className={cn("inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border", config.bg, config.color, className)}>
      {config.label}
    </span>
  );
}

export function StatusBadge({ status, className }: { status: Status; className?: string }) {
  const config = statusConfig[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", config.color, className)}>
      <span className={cn("w-1.5 h-1.5 rounded-full animate-pulse", config.dot)} />
      {config.label}
    </span>
  );
}

export function getPlatformLabel(platform: Platform): string {
  return platformConfig[platform]?.label ?? platform;
}
