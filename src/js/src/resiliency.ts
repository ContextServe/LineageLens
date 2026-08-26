import { ResiliencySignal } from "./model";

const DATA_WRITE_FUNCTIONS = new Set([
  "save", "delete", "destroy", "update", "insert", "execute", "query", "exec", "mutate"
]);

const BLOCKING_SYNC_FUNCTIONS = new Set([
  "readFileSync", "writeFileSync", "execSync", "execFileSync", "existsSync", "statSync"
]);

export function checkResiliencyRisks(
  functionName: string,
  line: number,
  isAsync: boolean
): ResiliencySignal[] {
  const signals: ResiliencySignal[] = [];

  if (DATA_WRITE_FUNCTIONS.has(functionName)) {
    signals.push({
      category: "data_write",
      severity: "review",
      evidence: {
        tier: "deterministic_heuristic",
        label: `rule match: ${functionName} at line ${line}`,
        confidence: null
      },
      line
    });
  }

  if (BLOCKING_SYNC_FUNCTIONS.has(functionName)) {
    signals.push({
      category: isAsync ? "blocking_in_async" : "blocking_operation",
      severity: isAsync ? "high" : "review",
      evidence: {
        tier: "deterministic_heuristic",
        label: `rule match: ${functionName} at line ${line}`,
        confidence: null
      },
      line
    });
  }

  return signals;
}
