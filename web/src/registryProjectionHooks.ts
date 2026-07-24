import { useQuery } from "@tanstack/react-query";
import { getJson } from "./api";
import type { RegistryProjectionSnapshot } from "./registryProjectionTypes";

const registryProjectionQueryDefaults = { staleTime: 2_000, retry: 1 } as const;

export const useRegistryProjection = () =>
  useQuery({
    queryKey: ["registry-projection"],
    queryFn: () => getJson<RegistryProjectionSnapshot>("/api/v1/registry-projection"),
    ...registryProjectionQueryDefaults
  });
