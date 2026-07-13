import { describe, expect, it } from "vitest";
import { computeDiff } from "../features/documents/DocumentDiff";

describe("document diff bounds", () => {
  it("reports exact small additions and removals", () => {
    const result = computeDiff("one\ntwo\nthree", "one\nchanged\nthree\nfour");
    expect(result.coarse).toBe(false);
    expect(result.added).toBe(2);
    expect(result.removed).toBe(1);
    expect(result.lines.filter((line) => line.kind === "added").map((line) => line.text)).toEqual(["changed", "four"]);
  });

  it("uses a bounded sample for a five-megabyte many-line document", () => {
    const before = "a\n".repeat(2_621_440);
    const after = `${before.slice(0, -2)}b\n`;
    const result = computeDiff(before, after);

    expect(result.coarse).toBe(true);
    expect(result.truncated).toBe(true);
    expect(result.lines.length).toBeLessThanOrEqual(480);
    expect(result.totalLines).toBeGreaterThan(5_000_000);
  });

  it("does not materialize hundreds of thousands of rows below the character budget", () => {
    const before = "\n".repeat(450_000);
    const result = computeDiff(before, `${before}changed`);

    expect(result.coarse).toBe(true);
    expect(result.truncated).toBe(true);
    expect(result.lines.length).toBeLessThanOrEqual(480);
    expect(result.totalLines).toBeGreaterThan(800_000);
  });
});
