#!/usr/bin/env node

import { Command } from "commander";
import * as path from "path";
import * as fs from "fs";
import { JsAstAnalyzer } from "./analyzer";

const program = new Command();

program
  .name("lineagelens-js")
  .description("LineageLens JavaScript and TypeScript Code Graph Analyzer")
  .version("0.1.0");

program
  .command("analyze [projectPath]")
  .description("Analyze JS/TS codebase and output graph.json")
  .option("-o, --output <path>", "Output path for graph.json", ".lineagelens/graph.json")
  .option("-q, --quiet", "Suppress summary output")
  .action((projectPathArg = ".", options) => {
    const projectRoot = path.resolve(projectPathArg);
    if (!fs.existsSync(projectRoot) || !fs.statSync(projectRoot).isDirectory()) {
      console.error(`Error: Target directory does not exist: ${projectRoot}`);
      process.exit(1);
    }

    if (!options.quiet) {
      console.log("LineageLens JS/TS Analyzer v0.1.0");
      console.log(`Analyzing JS/TS project: ${projectRoot}`);
    }

    const analyzer = new JsAstAnalyzer(projectRoot);
    const graph = analyzer.analyze();

    const outputPath = path.resolve(projectRoot, options.output);
    const outputDir = path.dirname(outputPath);

    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    fs.writeFileSync(outputPath, JSON.stringify(graph, null, 2), "utf-8");

    if (!options.quiet) {
      console.log(`✓ Successfully generated JS/TS code graph at: ${outputPath}`);
      console.log(`  Symbols found: ${graph.symbols.length}`);
      console.log(`  Containers found: ${graph.containers.length}`);
      console.log(`  Relations found: ${graph.relations.length}`);
    }
  });

program.parse(process.argv);
