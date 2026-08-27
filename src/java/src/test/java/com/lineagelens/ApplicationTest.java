package com.lineagelens;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

public class ApplicationTest {

    @Test
    public void testApplicationInstance() {
        Application app = new Application();
        assertNotNull(app);
        app.run();
    }

    @Test
    public void testApplicationMain(@TempDir Path tempDir) throws IOException {
        System.setProperty("lineagelens.test", "true");
        Files.createDirectories(tempDir.resolve("src/main/java"));
        Files.writeString(tempDir.resolve("src/main/java/A.java"), "public class A {}");

        Application.main(new String[]{tempDir.toString()});
        Application.main(new String[]{"analyze", tempDir.toString(), "-q"});
        Application.main(new String[]{"--help"});
        Application.main(new String[]{});
    }
}
