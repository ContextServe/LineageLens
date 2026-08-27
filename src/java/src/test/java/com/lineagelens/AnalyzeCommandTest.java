package com.lineagelens;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

public class AnalyzeCommandTest {

    @Test
    public void testAnalyzeCommandExecutionQuiet(@TempDir Path tempDir) throws IOException {
        Path srcDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(srcDir);
        Files.writeString(srcDir.resolve("Foo.java"), "package com.example; public class Foo {}");

        int exitCode = io.micronaut.configuration.picocli.PicocliRunner.execute(AnalyzeCommand.class, tempDir.toString(), "-o", ".lineagelens/graph.json", "-q");
        assertEquals(0, exitCode);

        Path graphFile = tempDir.resolve(".lineagelens/graph.json");
        assertTrue(Files.exists(graphFile));
    }

    @Test
    public void testAnalyzeCommandExecutionVerbose(@TempDir Path tempDir) throws IOException {
        Path srcDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(srcDir);
        Files.writeString(srcDir.resolve("Foo.java"), "package com.example; public class Foo {}");

        int exitCode = io.micronaut.configuration.picocli.PicocliRunner.execute(AnalyzeCommand.class, tempDir.toString(), "-o", ".lineagelens/graph.json");
        assertEquals(0, exitCode);

        Path graphFile = tempDir.resolve(".lineagelens/graph.json");
        assertTrue(Files.exists(graphFile));
    }

    @Test
    public void testAnalyzeCommandInvalidPath() {
        int exitCode = io.micronaut.configuration.picocli.PicocliRunner.execute(AnalyzeCommand.class, "/invalid/nonexistent/path/xyz");
        assertEquals(1, exitCode);
    }
}
