// The reads the shell and the homes share, one hook each, cached by TanStack Query under the route's path. Every
// type comes from the document (`optlab.server.payloads` describes the domain answers).
import { useQuery } from "@tanstack/react-query";
import { api, ok, type Schemas } from "./client";

export type MarketContext = Schemas["MarketContext"];
export type Trade = Schemas["Trade"];
export type QuoteItem = Schemas["QuoteItem"];

const MINUTE = 60_000;

export function useMarketContext() {
  return useQuery({
    queryKey: ["market", "context"],
    queryFn: () => ok(api.GET("/api/v1/market/context")),
    staleTime: 5 * MINUTE,
  });
}

export function useSnapshots() {
  return useQuery({
    queryKey: ["market", "snapshots"],
    queryFn: () => ok(api.GET("/api/v1/market/snapshots", { params: { query: { limit: 200 } } })),
    staleTime: 5 * MINUTE,
  });
}

export function useEngine() {
  return useQuery({
    queryKey: ["quant", "engine"],
    queryFn: () => ok(api.GET("/api/v1/quant/engine")),
    staleTime: 10 * MINUTE,
  });
}

export function useTrades() {
  return useQuery({
    queryKey: ["trading", "trades"],
    queryFn: () => ok(api.GET("/api/v1/trading/trades", { params: { query: { limit: 200 } } })),
    staleTime: MINUTE,
  });
}

export function useUpcoming(days = 90) {
  return useQuery({
    queryKey: ["trading", "upcoming", days],
    queryFn: () => ok(api.GET("/api/v1/trading/events/upcoming", { params: { query: { days } } })),
    staleTime: 5 * MINUTE,
  });
}

export function useQuotes() {
  return useQuery({
    queryKey: ["structuring", "quotes"],
    queryFn: () => ok(api.GET("/api/v1/structuring/quotes", { params: { query: { limit: 50 } } })),
    staleTime: MINUTE,
  });
}

/** A saved quote: its sheet and its result (Structuring's quote page, and the bridges that open on one). */
export function useSavedQuote(id: string | null | undefined) {
  return useQuery({
    queryKey: ["structuring", "quote", id],
    queryFn: () => ok(api.GET("/api/v1/structuring/quotes/{quote_id}", { params: { path: { quote_id: id ?? "" } } })),
    enabled: Boolean(id),
  });
}

export function usePresets() {
  return useQuery({
    queryKey: ["structuring", "presets"],
    queryFn: () => ok(api.GET("/api/v1/structuring/presets")),
    staleTime: 30 * MINUTE,
  });
}

export function useRiskRuns(kind?: string) {
  return useQuery({
    queryKey: ["risk", "runs", kind ?? "all"],
    queryFn: () => ok(api.GET("/api/v1/risk/runs", { params: { query: kind ? { kind, limit: 50 } : { limit: 50 } } })),
    staleTime: MINUTE,
  });
}

export function usePnl() {
  return useQuery({ queryKey: ["risk", "pnl"], queryFn: () => ok(api.GET("/api/v1/risk/pnl")), staleTime: MINUTE });
}

export function useLakeCoverage() {
  return useQuery({
    queryKey: ["market", "lake", "coverage"],
    queryFn: () => ok(api.GET("/api/v1/market/lake/coverage")),
    staleTime: 10 * MINUTE,
  });
}

export function useLakeQuality() {
  return useQuery({
    queryKey: ["market", "lake", "quality"],
    queryFn: () => ok(api.GET("/api/v1/market/lake/quality")),
    staleTime: 10 * MINUTE,
  });
}

export function useUniverse() {
  return useQuery({
    queryKey: ["market", "universe"],
    queryFn: () => ok(api.GET("/api/v1/market/universe")),
    staleTime: 30 * MINUTE,
  });
}

export function useSystem(enabled = true) {
  return useQuery({
    queryKey: ["admin", "system"],
    queryFn: () => ok(api.GET("/api/v1/admin/system")),
    enabled,
    refetchInterval: 15_000,
  });
}

export function useAdminJobs(enabled = true) {
  return useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: () => ok(api.GET("/api/v1/admin/jobs", { params: { query: { limit: 50 } } })),
    enabled,
    refetchInterval: 15_000,
  });
}
