import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, postJson } from "./api";
import type {
  SkillDetailSnapshot,
  SkillForkPreview,
  SkillsSnapshot,
  SkillSavePreview,
  SkillWriteResult
} from "./types";

const skillQueryDefaults = {
  staleTime: 1_000,
  retry: 1,
  refetchOnWindowFocus: true
} as const;

export const useSkills = () =>
  useQuery({
    queryKey: ["skills"],
    queryFn: () => getJson<SkillsSnapshot>("/api/v1/skills"),
    ...skillQueryDefaults
  });

export const useSkillDetail = (skillId: string | null, expectedCatalogSha256: string | null) =>
  useQuery({
    queryKey: ["skill", skillId, expectedCatalogSha256],
    queryFn: () =>
      getJson<SkillDetailSnapshot>(
        `/api/v1/skills/${encodeURIComponent(skillId ?? "")}?expected=${encodeURIComponent(expectedCatalogSha256 ?? "")}`
      ),
    enabled: Boolean(skillId && expectedCatalogSha256),
    ...skillQueryDefaults
  });

interface SkillSaveInput {
  skillId: string;
  content: string;
  expectedCatalogSha256: string;
  expectedSourceSha256: string;
  idempotencyKey: string;
}

interface SkillForkInput {
  skillId: string;
  newSkillId?: string;
  expectedCatalogSha256: string;
  expectedSourceSha256: string;
  idempotencyKey: string;
}

export const useSkillSavePreview = () =>
  useMutation({
    mutationFn: (input: SkillSaveInput) =>
      postJson<SkillSavePreview>(
        `/api/v1/skills/${encodeURIComponent(input.skillId)}/save/preview`,
        {
          content: input.content,
          expected_catalog_sha256: input.expectedCatalogSha256,
          expected_source_sha256: input.expectedSourceSha256
        },
        input.idempotencyKey
      )
  });

export const useSkillSave = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SkillSaveInput) =>
      postJson<SkillWriteResult>(
        `/api/v1/skills/${encodeURIComponent(input.skillId)}/save`,
        {
          content: input.content,
          expected_catalog_sha256: input.expectedCatalogSha256,
          expected_source_sha256: input.expectedSourceSha256
        },
        input.idempotencyKey
      ),
    onSuccess: async (_result, input) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["skills"] }),
        client.invalidateQueries({ queryKey: ["skill", input.skillId] }),
        client.invalidateQueries({ queryKey: ["catalog"] }),
        client.invalidateQueries({ queryKey: ["agents"] })
      ]);
    }
  });
};

export const useSkillForkPreview = () =>
  useMutation({
    mutationFn: (input: SkillForkInput) =>
      postJson<SkillForkPreview>(
        `/api/v1/skills/${encodeURIComponent(input.skillId)}/fork/preview`,
        {
          new_skill_id: input.newSkillId || null,
          expected_catalog_sha256: input.expectedCatalogSha256,
          expected_source_sha256: input.expectedSourceSha256
        },
        input.idempotencyKey
      )
  });

export const useSkillFork = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SkillForkInput & { newSkillId: string }) =>
      postJson<SkillWriteResult>(
        `/api/v1/skills/${encodeURIComponent(input.skillId)}/fork`,
        {
          new_skill_id: input.newSkillId,
          expected_catalog_sha256: input.expectedCatalogSha256,
          expected_source_sha256: input.expectedSourceSha256
        },
        input.idempotencyKey
      ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["skills"] }),
        client.invalidateQueries({ queryKey: ["catalog"] }),
        client.invalidateQueries({ queryKey: ["agents"] })
      ]);
    }
  });
};

