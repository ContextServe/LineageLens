import * as ts from "typescript";

const ROUTE_METHOD_NAMES = new Set([
  "get", "post", "put", "delete", "patch", "all", "use", "route"
]);

const ROUTE_DECORATORS = new Set([
  "Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All", "Controller"
]);

const TEST_CALL_NAMES = new Set(["test", "it", "describe"]);
const TEST_HOOK_NAMES = new Set(["beforeEach", "afterEach", "beforeAll", "afterAll"]);

export function detectEntryPoints(
  node: ts.Node,
  symbolName: string,
  decorators: string[],
  filePath: string
): string[] {
  const kinds: string[] = [];

  // Check route decorators (NestJS)
  if (decorators.some(d => ROUTE_DECORATORS.has(d))) {
    kinds.push("api_route");
  }

  // Check Next.js App Router exports (e.g. GET, POST, PUT, DELETE)
  if (filePath.includes("app/api/") || filePath.includes("pages/api/")) {
    if (["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "default"].includes(symbolName)) {
      kinds.push("api_route");
    }
  }

  // Check Express/Fastify route invocations: app.get(), router.post()
  if (ts.isCallExpression(node)) {
    const expr = node.expression;
    if (ts.isPropertyAccessExpression(expr)) {
      const methodName = expr.name.text.toLowerCase();
      if (ROUTE_METHOD_NAMES.has(methodName)) {
        kinds.push("api_route");
      }
    }
  }

  // Check test functions (Jest, Vitest, Mocha)
  if (TEST_CALL_NAMES.has(symbolName) || filePath.includes(".test.") || filePath.includes(".spec.")) {
    if (ts.isCallExpression(node)) {
      const expr = node.expression;
      if (ts.isIdentifier(expr) && TEST_CALL_NAMES.has(expr.text)) {
        kinds.push("test");
      }
      if (ts.isIdentifier(expr) && TEST_HOOK_NAMES.has(expr.text)) {
        kinds.push("test_fixture");
      }
    }
    if (symbolName.startsWith("test") || symbolName.startsWith("spec")) {
      kinds.push("test");
    }
  }

  // Check UI entry points (React page components)
  if ((filePath.includes("pages/") || filePath.includes("app/")) && (symbolName === "default" || symbolName.endsWith("Page"))) {
    kinds.push("ui_entry");
  }

  return kinds;
}
