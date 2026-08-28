import * as ts from "typescript";
import { detectEntryPoints } from "../src/entrypoints";

describe("detectEntryPoints", () => {
  it("detects NestJS route decorators", () => {
    const dummyNode = ts.factory.createIdentifier("dummy");
    const kinds = detectEntryPoints(dummyNode, "getUsers", ["Get"], "src/users.ts");
    expect(kinds).toContain("api_route");
  });

  it("detects Next.js route exports", () => {
    const dummyNode = ts.factory.createIdentifier("dummy");
    const kinds = detectEntryPoints(dummyNode, "GET", [], "src/app/api/users/route.ts");
    expect(kinds).toContain("api_route");
  });

  it("detects Express call expressions", () => {
    const sf = ts.createSourceFile("server.ts", "app.get('/api', handler)", ts.ScriptTarget.ES2022);
    const exprStmt = sf.statements[0] as ts.ExpressionStatement;
    const callExpr = exprStmt.expression as ts.CallExpression;

    const kinds = detectEntryPoints(callExpr, "get", [], "src/server.ts");
    expect(kinds).toContain("api_route");
  });

  it("detects test functions and test files", () => {
    const sf = ts.createSourceFile("user.test.ts", "test('should work', () => {})", ts.ScriptTarget.ES2022);
    const exprStmt = sf.statements[0] as ts.ExpressionStatement;
    const callExpr = exprStmt.expression as ts.CallExpression;

    const kinds = detectEntryPoints(callExpr, "test", [], "src/user.test.ts");
    expect(kinds).toContain("test");
  });

  it("detects UI page components", () => {
    const dummyNode = ts.factory.createIdentifier("dummy");
    const kinds = detectEntryPoints(dummyNode, "default", [], "src/pages/index.tsx");
    expect(kinds).toContain("ui_entry");
  });
});
