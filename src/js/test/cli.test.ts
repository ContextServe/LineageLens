import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import { execSync } from "child_process";

describe("CLI Launcher", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "lineagelens-cli-test-"));
  });

  afterEach(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });

  it("runs analysis via CLI and generates graph.json", () => {
    const srcDir = path.join(tempDir, "src");
    fs.mkdirSync(srcDir, { recursive: true });
    fs.writeFileSync(path.join(srcDir, "app.ts"), "export function hello(): string { return 'world'; }");

    const cliPath = path.resolve(__dirname, "../src/cli.ts");
    const nodeBin = process.execPath;
    const tsNodeBin = path.resolve(__dirname, "../node_modules/.bin/ts-node");

    const cmd = `"${tsNodeBin}" "${cliPath}" analyze "${tempDir}" -q`;
    execSync(cmd, { stdio: "pipe" });

    const graphPath = path.join(tempDir, ".lineagelens", "graph.json");
    expect(fs.existsSync(graphPath)).toBe(true);

    const raw = JSON.parse(fs.readFileSync(graphPath, "utf-8"));
    expect(raw.schema_version).toBe(2);
    expect(raw.symbols.length).toBeGreaterThan(0);
  });
});
