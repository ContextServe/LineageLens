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
                import java.util.concurrent.CompletableFuture;
                import java.util.List;

                public class UserService extends BaseService implements IUserService {

                    @GetMapping("/users")
                    public CompletableFuture<List<String>> getUsers() {
                        return fetchFromDatabase();
                    }

                    @Override
                    public List<String> process() {
                        return List.of();
                    }

                    private CompletableFuture<List<String>> fetchFromDatabase() {
                        return CompletableFuture.completedFuture(List.of("Alice", "Bob"));
                    }
                }

                abstract class BaseService {}
                interface IUserService {}
                enum UserRole { ADMIN, USER }
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
        assertTrue(classSymbol.getBases().contains("BaseService"));
        assertTrue(classSymbol.getBases().contains("IUserService"));

        // Verify Interface & Enum
        Symbol intfSymbol = graph.getSymbol("com.example.sample.IUserService");
        assertNotNull(intfSymbol);
        assertEquals("interface", intfSymbol.getKind());

        Symbol enumSymbol = graph.getSymbol("com.example.sample.UserRole");
        assertNotNull(enumSymbol);
        assertEquals("enum", enumSymbol.getKind());

        // Verify Method Symbol & Async Detection
        Symbol methodSymbol = graph.getSymbol("com.example.sample.UserService.getUsers");
        assertNotNull(methodSymbol);
        assertEquals("method", methodSymbol.getKind());
        assertEquals("api_route", methodSymbol.getEntryPoint());
        assertTrue(methodSymbol.isAsync_());

        // Verify Relations (INHERITS & AWAIT_CALLS)
        assertFalse(graph.getRelations().isEmpty());
        boolean hasInherits = graph.getRelations().stream().anyMatch(r ->
                "com.example.sample.UserService".equals(r.getSource()) &&
                "com.example.sample.BaseService".equals(r.getTarget()) &&
                "INHERITS".equals(r.getKind())
        );
        assertTrue(hasInherits, "Expected INHERITS relation from UserService to BaseService");

        boolean hasCall = graph.getRelations().stream().anyMatch(r ->
                "com.example.sample.UserService.getUsers".equals(r.getSource()) &&
                "com.example.sample.UserService.fetchFromDatabase".equals(r.getTarget())
        );
        assertTrue(hasCall, "Expected CALLS relation from getUsers to fetchFromDatabase");
    }
}
