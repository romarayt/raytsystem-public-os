import { describe, expect, it } from "vitest";
import { formatDate } from "../api";

describe("formatDate", () => {
  it("never crashes when an untrusted detail value is not a timestamp", () => {
    expect(formatDate("supported")).toBe("supported");
  });

  it("uses the empty-state copy when no timestamp exists", () => {
    expect(formatDate(null)).toBe("Не указано");
  });
});
