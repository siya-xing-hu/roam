import { describe, expect, it } from "vitest";
import { nextBoundaryLevel } from "../src/config";

describe("nextBoundaryLevel", () => {
  it("loads city files after selecting a regular province", () => {
    expect(nextBoundaryLevel("country", "320000")).toBe("city");
  });

  it("loads county files immediately for municipalities", () => {
    expect(nextBoundaryLevel("country", "310000")).toBe("district");
  });

  it("loads county files after selecting a prefecture-level city", () => {
    expect(nextBoundaryLevel("province", "320100")).toBe("district");
  });

  it("does not drill below the county map", () => {
    expect(nextBoundaryLevel("city", "320102")).toBeNull();
  });
});
