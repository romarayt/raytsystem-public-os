import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, postJson } from "./api";
import type {
  EmergencyAction,
  EmergencyReceipt,
  PlatformFeatures,
  PolicySimulation,
  SystemSectionId,
  SystemSectionSnapshot,
  TraceDetail
} from "./featureTypes";

const featureQueryDefaults = { staleTime: 2_000, retry: 1 } as const;

export const usePlatformFeatures = () =>
  useQuery({
    queryKey: ["platform-features"],
    queryFn: () => getJson<PlatformFeatures>("/api/v1/features"),
    ...featureQueryDefaults
  });

export const useSystemSection = (section: SystemSectionId) =>
  useQuery({
    queryKey: ["systems", section],
    queryFn: () => getJson<SystemSectionSnapshot>(`/api/v1/systems/${section}`),
    ...featureQueryDefaults
  });

export const useTraceDetail = (traceId: string | null) =>
  useQuery({
    queryKey: ["trace-detail", traceId],
    queryFn: () => getJson<TraceDetail>(`/api/v1/traces/${encodeURIComponent(traceId ?? "")}`),
    enabled: Boolean(traceId),
    ...featureQueryDefaults
  });

export const usePolicySimulation = () =>
  useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      postJson<PolicySimulation>("/api/v1/policy-simulations", payload)
  });

export const useEmergencyCommand = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      actions: EmergencyAction[];
      reason: string;
      approval_id: string | null;
      expected_snapshot_id: string;
    }) => postJson<EmergencyReceipt>("/api/v1/emergency-commands", payload),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["platform-features"] }),
        client.invalidateQueries({ queryKey: ["systems", "policies"] })
      ]);
    }
  });
};

export const useNotificationTransition = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      notification_id: string;
      state: "read" | "acknowledged" | "resolved";
      expected_snapshot_id: string;
    }) =>
      postJson<Record<string, unknown>>(
        `/api/v1/notifications/${encodeURIComponent(payload.notification_id)}/transitions`,
        { state: payload.state, expected_snapshot_id: payload.expected_snapshot_id }
      ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["platform-features"] }),
        client.invalidateQueries({ queryKey: ["systems", "notifications"] })
      ]);
    }
  });
};
