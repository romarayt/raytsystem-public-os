import { describe, expect, it } from "vitest";
import {
  canonicalAgentName,
  canonicalSkillName,
  catalogDescription,
  roleLabel,
  statusLabel
} from "../presentation";

describe("canonical catalog names", () => {
  it("keeps agent identities in English", () => {
    expect(canonicalAgentName({ agent_id: "agent_builder", name: "Builder" })).toBe("Builder");
    expect(canonicalAgentName({ agent_id: "agent_researcher", name: "Researcher" })).toBe("Researcher");
  });

  it("keeps skill identities unchanged", () => {
    expect(canonicalSkillName({ skill_id: "raytsystem-watch" })).toBe("raytsystem-watch");
    expect(canonicalSkillName({ skill_id: "raytsystem-query" })).toBe("raytsystem-query");
  });

  it("localizes roles, statuses and descriptions separately", () => {
    expect(roleLabel("builder")).toBe("реализация");
    expect(statusLabel("disabled")).toBe("Отключено");
    expect(catalogDescription("agent_builder", "fallback")).toMatch(/реализац/i);
  });
});
