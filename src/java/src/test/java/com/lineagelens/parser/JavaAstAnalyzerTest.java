package com.lineagelens.parser;

import com.lineagelens.model.CodeGraph;
import com.lineagelens.model.Symbol;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

public class JavaAstAnalyzerTest {

    @Test
    public void testJavaCodeGraphGeneration(@TempDir Path tempDir) throws IOException {
        // Create sample Java file
        Path pkgDir = tempDir.resolve("src/main/java/com/example/sample");
        Files.createDirectories(pkgDir);

        Path serviceFile = pkgDir.resolve("UserService.java");
        String serviceCode = """
                package com.example.sample;

                import org.springframework.web.bind.annotation.GetMapping;
                import java.util.List;

                public class UserService {

                    @GetMapping("/users")
                    public List<String> getUsers() {
                        return fetchFromDatabase();
                    }

                    private List<String> fetchFromDatabase() {
                        return List.of("Alice", "Bob");
                    }
                }
                """;
        Files.writeString(serviceFile, serviceCode);

        // Run analysis
        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        assertNotNull(graph);
        assertEquals(CodeGraph.SCHEMA_VERSION, graph.getSchemaVersion());

        // Verify Class Symbol
        Symbol classSymbol = graph.getSymbol("com.example.sample.UserService");
        assertNotNull(classSymbol);
        assertEquals("class", classSymbol.getKind());
        assertEquals("UserService", classSymbol.getName());

        // Verify Method Symbol
        Symbol methodSymbol = graph.getSymbol("com.example.sample.UserService.getUsers");
        assertNotNull(methodSymbol);
        assertEquals("method", methodSymbol.getKind());
        assertEquals("api_route", methodSymbol.getEntryPoint());

        // Verify Call Relation
        assertFalse(graph.getRelations().isEmpty());
        boolean hasCall = graph.getRelations().stream().anyMatch(r ->
                "com.example.sample.UserService.getUsers".equals(r.getSource()) &&
                "com.example.sample.UserService.fetchFromDatabase".equals(r.getTarget())
        );
        assertTrue(hasCall, "Expected CALLS relation from getUsers to fetchFromDatabase");
    }
}
