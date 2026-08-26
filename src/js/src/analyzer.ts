import * as ts from "typescript";
import * as path from "path";
import * as fs from "fs";
import { CodeGraph, Container, Symbol, Relation, SCHEMA_VERSION } from "./model";
import { detectEntryPoints } from "./entrypoints";
import { checkResiliencyRisks } from "./resiliency";

export class JsAstAnalyzer {
  private projectRoot: string;
  private program: ts.Program;
  private typeChecker: ts.TypeChecker;

  constructor(projectRoot: string) {
    this.projectRoot = path.resolve(projectRoot);
    const files = this.collectFiles(this.projectRoot);

    const compilerOptions: ts.CompilerOptions = {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      allowJs: true,
      checkJs: false,
      skipLibCheck: true
    };

    this.program = ts.createProgram(files, compilerOptions);
    this.typeChecker = this.program.getTypeChecker();
  }

  private collectFiles(dir: string): string[] {
    const files: string[] = [];
    if (!fs.existsSync(dir)) return files;

    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!["node_modules", ".git", "dist", "build", "coverage", ".next"].includes(entry.name)) {
          files.push(...this.collectFiles(fullPath));
        }
      } else if (/\.(js|jsx|ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
        files.push(fullPath);
      }
    }
    return files;
  }

  public analyze(): CodeGraph {
    const symbolsMap = new Map<string, Symbol>();
    const containersMap = new Map<string, Container>();
    const relations: Relation[] = [];

    const sourceFiles = this.program.getSourceFiles().filter(
      sf => !sf.isDeclarationFile && sf.fileName.startsWith(this.projectRoot)
    );

    // Phase 1: Containers & Symbol Definitions
    for (const sf of sourceFiles) {
      const relPath = path.relative(this.projectRoot, sf.fileName).replace(/\\/g, "/");
      const moduleName = relPath.replace(/\.(js|jsx|ts|tsx)$/, "").replace(/\//g, ".");

      // Ensure module container
      if (!containersMap.has(moduleName)) {
        containersMap.set(moduleName, {
          id: moduleName,
          kind: "module",
          name: path.basename(relPath),
          file: relPath,
          parent: null,
          children: []
        });
      }

      this.visitDefinitions(sf, sf, relPath, moduleName, symbolsMap, containersMap);
    }

    // Phase 2: Relations (CALLS, INHERITS, EXPORTS, IMPORTS, DECORATES)
    for (const sf of sourceFiles) {
      const relPath = path.relative(this.projectRoot, sf.fileName).replace(/\\/g, "/");
      const moduleName = relPath.replace(/\.(js|jsx|ts|tsx)$/, "").replace(/\//g, ".");

      this.visitRelationships(sf, sf, relPath, moduleName, symbolsMap, relations);
    }

    return {
      schema_version: SCHEMA_VERSION,
      project_root: this.projectRoot,
      symbols: Array.from(symbolsMap.values()),
      containers: Array.from(containersMap.values()),
      relations
    };
  }

  private visitDefinitions(
    node: ts.Node,
    sf: ts.SourceFile,
    relPath: string,
    moduleName: string,
    symbolsMap: Map<string, Symbol>,
    containersMap: Map<string, Container>,
    parentSymbolId: string | null = null
  ) {
    const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
    const endLine = sf.getLineAndCharacterOfPosition(node.getEnd()).line + 1;

    // Function or Method Declaration
    if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) {
      const funcName = node.name.getText(sf);
      const symbolId = parentSymbolId ? `${parentSymbolId}.${funcName}` : `${moduleName}.${funcName}`;
      const isAsync = Boolean(node.modifiers?.some(m => m.kind === ts.SyntaxKind.AsyncKeyword));

      const symbol: Symbol = {
        id: symbolId,
        kind: parentSymbolId ? "method" : "function",
        name: funcName,
        file: relPath,
        line,
        end_line: endLine,
        module: moduleName,
        parent: parentSymbolId || moduleName,
        async_: isAsync,
        inputs: node.parameters.map(p => ({
          name: p.name.getText(sf),
          type: p.type ? p.type.getText(sf) : "unknown",
          default: p.initializer ? p.initializer.getText(sf) : null
        })),
        outputs: [{
          type: node.type ? node.type.getText(sf) : (isAsync ? "Promise<void>" : "void"),
          evidence: "annotation"
        }],
        decorators: this.getDecorators(node, sf),
        bases: [],
        entry_point_kinds: [],
        is_abstract: Boolean(node.modifiers?.some(m => m.kind === ts.SyntaxKind.AbstractKeyword)),
        resiliency: []
      };

      const entryKinds = detectEntryPoints(node, funcName, symbol.decorators, relPath);
      entryKinds.forEach(k => {
        if (!symbol.entry_point_kinds.includes(k)) symbol.entry_point_kinds.push(k);
      });
      if (symbol.entry_point_kinds.length > 0) symbol.entry_point = symbol.entry_point_kinds[0];

      symbolsMap.set(symbolId, symbol);
      containersMap.get(moduleName)?.children.push(symbolId);

      ts.forEachChild(node, child => this.visitDefinitions(child, sf, relPath, moduleName, symbolsMap, containersMap, symbolId));
      return;
    }

    // Class Declaration
    if (ts.isClassDeclaration(node) && node.name) {
      const className = node.name.text;
      const symbolId = `${moduleName}.${className}`;

      const bases: string[] = [];
      if (node.heritageClauses) {
        for (const hc of node.heritageClauses) {
          for (const type of hc.types) {
            bases.push(type.expression.getText(sf));
          }
        }
      }

      const symbol: Symbol = {
        id: symbolId,
        kind: "class",
        name: className,
        file: relPath,
        line,
        end_line: endLine,
        module: moduleName,
        parent: moduleName,
        async_: false,
        inputs: [],
        outputs: [],
        decorators: this.getDecorators(node, sf),
        bases,
        entry_point_kinds: [],
        is_abstract: Boolean(node.modifiers?.some(m => m.kind === ts.SyntaxKind.AbstractKeyword)),
        resiliency: []
      };

      symbolsMap.set(symbolId, symbol);
      containersMap.get(moduleName)?.children.push(symbolId);

      ts.forEachChild(node, child => this.visitDefinitions(child, sf, relPath, moduleName, symbolsMap, containersMap, symbolId));
      return;
    }

    // Interface Declaration
    if (ts.isInterfaceDeclaration(node)) {
      const name = node.name.text;
      const symbolId = `${moduleName}.${name}`;
      const symbol: Symbol = {
        id: symbolId,
        kind: "interface",
        name,
        file: relPath,
        line,
        end_line: endLine,
        module: moduleName,
        parent: moduleName,
        async_: false,
        inputs: [],
        outputs: [],
        decorators: [],
        bases: node.heritageClauses ? node.heritageClauses.flatMap(hc => hc.types.map(t => t.expression.getText(sf))) : [],
        entry_point_kinds: [],
        is_abstract: true,
        resiliency: []
      };
      symbolsMap.set(symbolId, symbol);
      return;
    }

    ts.forEachChild(node, child => this.visitDefinitions(child, sf, relPath, moduleName, symbolsMap, containersMap, parentSymbolId));
  }

  private visitRelationships(
    node: ts.Node,
    sf: ts.SourceFile,
    relPath: string,
    moduleName: string,
    symbolsMap: Map<string, Symbol>,
    relations: Relation[],
    currentSymbolId: string | null = null
  ) {
    let activeSymbolId = currentSymbolId;

    if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) {
      activeSymbolId = currentSymbolId ? `${currentSymbolId}.${node.name.getText(sf)}` : `${moduleName}.${node.name.getText(sf)}`;
    } else if (ts.isClassDeclaration(node) && node.name) {
      activeSymbolId = `${moduleName}.${node.name.text}`;

      // INHERITS relations
      const symbol = symbolsMap.get(activeSymbolId);
      if (symbol) {
        for (const baseName of symbol.bases) {
          const targetId = this.findSymbolIdByName(baseName, symbolsMap);
          if (targetId) {
            const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
            relations.push({
              source: activeSymbolId,
              target: targetId,
              kind: "INHERITS",
              file: relPath,
              line,
              evidence: { tier: "deterministic_fact", label: "static_ast" },
              resolution: "resolved",
              resolution_evidence: { tier: "deterministic_fact", label: "static_scope_walk" },
              arguments: []
            });
          }
        }
      }
    }

    // Call Expression -> CALLS / AWAIT_CALLS relation
    if (ts.isCallExpression(node) && activeSymbolId) {
      const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
      const calledName = node.expression.getText(sf);
      const funcName = calledName.includes(".") ? calledName.split(".").pop()! : calledName;

      // Resiliency check
      const currentSymbol = symbolsMap.get(activeSymbolId);
      if (currentSymbol) {
        const risks = checkResiliencyRisks(funcName, line, currentSymbol.async_);
        risks.forEach(r => currentSymbol.resiliency.push(r));
      }

      const targetSymbolId = this.findSymbolIdByName(funcName, symbolsMap);
      if (targetSymbolId) {
        const isAwait = node.parent && ts.isAwaitExpression(node.parent);
        relations.push({
          source: activeSymbolId,
          target: targetSymbolId,
          kind: isAwait ? "AWAIT_CALLS" : "CALLS",
          file: relPath,
          line,
          evidence: { tier: "deterministic_fact", label: "static_ast" },
          resolution: "resolved",
          resolution_evidence: { tier: "deterministic_fact", label: "static_scope_walk" },
          arguments: []
        });
      }
    }

    ts.forEachChild(node, child => this.visitRelationships(child, sf, relPath, moduleName, symbolsMap, relations, activeSymbolId));
  }

  private findSymbolIdByName(name: string, symbolsMap: Map<string, Symbol>): string | null {
    for (const symbol of symbolsMap.values()) {
      if (symbol.name === name) return symbol.id;
    }
    return null;
  }

  private getDecorators(node: ts.Node, sf: ts.SourceFile): string[] {
    const decorators: string[] = [];
    const extractName = (expr: ts.Expression): string => {
      if (ts.isCallExpression(expr)) {
        return extractName(expr.expression);
      }
      return expr.getText(sf).replace(/^@/, "");
    };

    if (ts.canHaveDecorators(node)) {
      const decs = ts.getDecorators(node);
      if (decs) {
        for (const dec of decs) {
          decorators.push(extractName(dec.expression));
        }
      }
    }
    if (node.modifiers) {
      for (const mod of node.modifiers) {
        if (ts.isDecorator(mod)) {
          decorators.push(extractName(mod.expression));
        }
      }
    }
    return Array.from(new Set(decorators));
  }
}
