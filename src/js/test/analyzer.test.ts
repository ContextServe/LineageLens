import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import { JsAstAnalyzer } from "../src/analyzer";

describe("JsAstAnalyzer", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "lineagelens-js-test-"));
  });

  afterEach(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it("analyzes classes, interfaces, methods, calls, inherits, and decorators", () => {
    const srcDir = path.join(tempDir, "src");
    fs.mkdirSync(srcDir, { recursive: true });

    const code = `
      import { Get } from "@nestjs/common";

      export interface IService {
        fetch(): Promise<void>;
      }

      export class BaseService {
        protected log() {}
      }

      export class UserService extends BaseService implements IService {
        @Get("/users")
        public async fetch(): Promise<void> {
          this.log();
          await this.saveData();
        }

        private async saveData(): Promise<void> {
          const fs = require("fs");
          fs.readFileSync("file.txt");
        }
      }

      export function standaloneFunc(x: number = 5): number {
        return x;
      }
    `;

    fs.writeFileSync(path.join(srcDir, "userService.ts"), code, "utf-8");

    const analyzer = new JsAstAnalyzer(tempDir);
    const graph = analyzer.analyze();

    expect(graph.schema_version).toBe(2);
    expect(graph.project_root).toBe(tempDir);
    expect(graph.symbols.length).toBeGreaterThanOrEqual(4);

    // Verify Symbols
    const classSym = graph.symbols.find(s => s.name === "UserService");
    expect(classSym).toBeDefined();
    expect(classSym?.kind).toBe("class");
    expect(classSym?.bases).toContain("BaseService");
    expect(classSym?.bases).toContain("IService");

    const methodSym = graph.symbols.find(s => s.name === "fetch");
    expect(methodSym).toBeDefined();
    expect(methodSym?.kind).toBe("method");
    expect(methodSym?.async_).toBe(true);
    expect(methodSym?.entry_point).toBe("api_route");
    expect(methodSym?.decorators).toContain("Get");

    const standaloneSym = graph.symbols.find(s => s.name === "standaloneFunc");
    expect(standaloneSym).toBeDefined();
    expect(standaloneSym?.inputs[0].name).toBe("x");

    // Verify Relations
    const inheritsRel = graph.relations.find(r => r.kind === "INHERITS");
    expect(inheritsRel).toBeDefined();
    expect(inheritsRel?.source).toBe("src.userService.UserService");
    expect(inheritsRel?.target).toBe("src.userService.BaseService");

    const callRel = graph.relations.find(r => r.kind === "CALLS");
    expect(callRel).toBeDefined();

    const awaitCallRel = graph.relations.find(r => r.kind === "AWAIT_CALLS");
    expect(awaitCallRel).toBeDefined();
    expect(awaitCallRel?.source).toBe("src.userService.UserService.fetch");
    expect(awaitCallRel?.target).toBe("src.userService.UserService.saveData");

    // Verify Resiliency
    const saveSymbol = graph.symbols.find(s => s.name === "saveData");
    expect(saveSymbol?.resiliency.length).toBeGreaterThan(0);
  });
});
