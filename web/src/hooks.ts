import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, postJson } from "./api";
import type {
  CatalogSnapshot,
  CodeGraphQueryResult,
  CodeGraphStatus,
  DocumentGraphSlice,
  GraphSnapshot,
  HandbookArticle,
  HandbookTree,
  KnowledgeSnapshot,
  RunList,
  SearchResult,
  SystemSnapshot,
  TaskBoard
} from "./types";

const queryDefaults = { staleTime: 1_000, retry: 1 } as const;

export const useSystem = () =>
  useQuery({ queryKey: ["system"], queryFn: () => getJson<SystemSnapshot>("/api/v1/system"), ...queryDefaults });

export const useTasks = () =>
  useQuery({ queryKey: ["tasks"], queryFn: () => getJson<TaskBoard>("/api/v1/tasks"), ...queryDefaults });

export const useCatalog = () =>
  useQuery({ queryKey: ["catalog"], queryFn: () => getJson<CatalogSnapshot>("/api/v1/catalog"), ...queryDefaults });

export const useRuns = () =>
  useQuery({ queryKey: ["runs"], queryFn: () => getJson<RunList>("/api/v1/runs"), ...queryDefaults });

export const useUniverse = () =>
  useQuery({
    queryKey: ["universe"],
    queryFn: () => getJson<GraphSnapshot>("/api/v1/universe"),
    staleTime: 2_000,
    retry: 1
  });

export const useDocumentGraph = (documentId: string | null) =>
  useQuery({
    queryKey: ["documents", "graph", documentId],
    queryFn: () =>
      getJson<DocumentGraphSlice>(
        `/api/v1/documents/${encodeURIComponent(documentId ?? "")}/graph`
      ),
    enabled: Boolean(documentId),
    staleTime: 2_000,
    retry: 1
  });

export const useCodeGraphStatus = (enabled = true) =>
  useQuery({
    queryKey: ["code-graph", "status"],
    queryFn: () => getJson<CodeGraphStatus>("/api/v1/code-graph/status"),
    enabled,
    staleTime: 4_000,
    retry: 1,
    refetchOnWindowFocus: true
  });

export const useCodeGraphQuery = () =>
  useMutation({
    mutationFn: (payload: { query: string; expected_snapshot_id: string; depth: number }) =>
      postJson<CodeGraphQueryResult>("/api/v1/code-graph/query", payload)
  });

export const useCodeGraphTraversal = (operation: "neighbors" | "path" | "impact") =>
  useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      postJson<CodeGraphQueryResult>(`/api/v1/code-graph/${operation}`, payload)
  });

export const useCodeGraphMutation = (operation: "update" | "rebuild") => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: { expected_snapshot_id: string | null }) =>
      postJson<Record<string, unknown>>(`/api/v1/code-graph/${operation}`, payload),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["code-graph"] }),
        client.invalidateQueries({ queryKey: ["universe"] }),
        client.invalidateQueries({ queryKey: ["system"] })
      ]);
    }
  });
};

export const useKnowledge = () =>
  useQuery({
    queryKey: ["knowledge"],
    queryFn: () => getJson<KnowledgeSnapshot>("/api/v1/knowledge"),
    ...queryDefaults
  });

export const useHandbook = () =>
  useQuery({
    queryKey: ["handbook"],
    queryFn: () => getJson<HandbookTree>("/api/v1/handbook"),
    ...queryDefaults
  });

export const useHandbookArticle = (slug: string | null) =>
  useQuery({
    queryKey: ["handbook-article", slug],
    queryFn: () =>
      getJson<HandbookArticle>(`/api/v1/handbook/article?slug=${encodeURIComponent(slug ?? "/")}`),
    enabled: Boolean(slug),
    ...queryDefaults
  });

export const useGlobalSearch = (query: string) =>
  useQuery({
    queryKey: ["search", query],
    queryFn: () =>
      getJson<{ graph_snapshot_id: string; results: SearchResult[] }>(
        `/api/v1/search?q=${encodeURIComponent(query)}&limit=16`
      ),
    enabled: query.trim().length > 1,
    staleTime: 10_000
  });
