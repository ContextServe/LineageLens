package com.lineagelens;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.lineagelens.model.CodeGraph;
import com.lineagelens.parser.JavaAstAnalyzer;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.Parameters;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.concurrent.Callable;

@Command(name = "analyze", description = "Analyzes Java codebase and outputs LineageLens graph.json", mixinStandardHelpOptions = true)
public class AnalyzeCommand implements Callable<Integer> {

    @Parameters(index = "0", description = "Target project directory path", defaultValue = ".")
    private String projectPathStr;

    @Option(names = {"-o", "--output"}, description = "Output path for graph.json", defaultValue = ".lineagelens/graph.json")
    private String outputPathStr;

    @Option(names = {"-q", "--quiet"}, description = "Suppress summary output")
    private boolean quiet;

    @Override
    public Integer call() throws Exception {
        Path projectRoot = Paths.get(projectPathStr).toAbsolutePath().normalize();
        if (!Files.exists(projectRoot) || !Files.isDirectory(projectRoot)) {
            System.err.println("Error: Target directory does not exist: " + projectRoot);
            return 1;
        }

        if (!quiet) {
            System.out.println("LineageLens Java Analyzer v0.1.0");
            System.out.println("Analyzing Java project: " + projectRoot);
        }

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(projectRoot);
        CodeGraph graph = analyzer.analyze();

        Path outputPath = projectRoot.resolve(outputPathStr).normalize();
        if (outputPath.getParent() != null) {
            Files.createDirectories(outputPath.getParent());
        }

        ObjectMapper mapper = new ObjectMapper();
        mapper.enable(SerializationFeature.INDENT_OUTPUT);
        mapper.writeValue(outputPath.toFile(), graph);

        if (!quiet) {
            System.out.println("✓ Successfully generated Java code graph at: " + outputPath);
            System.out.println("  Symbols found: " + graph.getSymbols().size());
            System.out.println("  Containers found: " + graph.getContainers().size());
            System.out.println("  Relations found: " + graph.getRelations().size());
        }

        return 0;
    }
}
