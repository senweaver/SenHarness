import type { ProviderCatalogEntry } from "@/hooks/use-providers";

function isZh(locale: string): boolean {
  return locale.toLowerCase().startsWith("zh");
}

export function labelOf(entry: ProviderCatalogEntry, locale: string): string {
  if (isZh(locale)) {
    return entry.display_name_zh || entry.display_name;
  }
  return entry.display_name || entry.display_name_zh;
}

export function descOf(entry: ProviderCatalogEntry, locale: string): string {
  if (isZh(locale)) {
    return entry.description_zh || entry.description;
  }
  return entry.description || entry.description_zh;
}
