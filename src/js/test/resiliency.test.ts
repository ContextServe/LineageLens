import { checkResiliencyRisks } from "../src/resiliency";

describe("checkResiliencyRisks", () => {
  it("detects data write operations", () => {
    const risks = checkResiliencyRisks("save", 10, false);
    expect(risks).toHaveLength(1);
    expect(risks[0].category).toBe("data_write");
    expect(risks[0].severity).toBe("review");
  });

  it("detects blocking sync calls in sync vs async functions", () => {
    const syncRisks = checkResiliencyRisks("readFileSync", 15, false);
    expect(syncRisks).toHaveLength(1);
    expect(syncRisks[0].category).toBe("blocking_operation");
    expect(syncRisks[0].severity).toBe("review");

    const asyncRisks = checkResiliencyRisks("readFileSync", 20, true);
    expect(asyncRisks).toHaveLength(1);
    expect(asyncRisks[0].category).toBe("blocking_in_async");
    expect(asyncRisks[0].severity).toBe("high");
  });

  it("returns empty array for normal functions", () => {
    const risks = checkResiliencyRisks("calculateTotal", 5, false);
    expect(risks).toHaveLength(0);
  });
});
