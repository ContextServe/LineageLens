package com.lineagelens.parser;

import com.lineagelens.model.Symbol;
import org.eclipse.jdt.core.dom.AST;
import org.eclipse.jdt.core.dom.ASTParser;
import org.eclipse.jdt.core.dom.CompilationUnit;
import org.eclipse.jdt.core.dom.MethodDeclaration;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

public class JavaEntryPointDetectorTest {

    private MethodDeclaration parseMethod(String source) {
        ASTParser parser = ASTParser.newParser(AST.JLS17);
        parser.setSource(source.toCharArray());
        parser.setKind(ASTParser.K_COMPILATION_UNIT);

        CompilationUnit cu = (CompilationUnit) parser.createAST(null);
        return (MethodDeclaration) ((org.eclipse.jdt.core.dom.TypeDeclaration) cu.types().get(0)).getMethods()[0];
    }

    @Test
    public void testDetectAllAnnotationTypes() {
        String code = """
                public class FullController {
                    public static void main(String[] args) {}

                    @org.springframework.web.bind.annotation.PostMapping
                    public void post() {}

                    @org.springframework.web.bind.annotation.PutMapping
                    public void put() {}

                    @org.springframework.web.bind.annotation.DeleteMapping
                    public void delete() {}

                    @org.springframework.web.bind.annotation.PatchMapping
                    public void patch() {}

                    @org.springframework.web.bind.annotation.RequestMapping
                    public void req() {}

                    @jakarta.ws.rs.Path("/api")
                    public void path() {}

                    @org.junit.jupiter.api.ParameterizedTest
                    public void paramTest() {}

                    @org.junit.jupiter.api.RepeatedTest(5)
                    public void repeatTest() {}

                    @org.junit.jupiter.api.TestFactory
                    public void factoryTest() {}

                    @org.junit.jupiter.api.AfterEach
                    public void tearDown() {}

                    @org.junit.jupiter.api.BeforeAll
                    public void beforeAll() {}

                    @org.junit.jupiter.api.AfterAll
                    public void afterAll() {}

                    @jakarta.annotation.PostConstruct
                    public void init() {}

                    @jakarta.annotation.PreDestroy
                    public void destroy() {}

                    @org.springframework.context.event.EventListener
                    public void onEvent() {}

                    @org.springframework.scheduling.annotation.ScheduledMethod
                    public void sched() {}
                }
                """;

        ASTParser parser = ASTParser.newParser(AST.JLS17);
        parser.setSource(code.toCharArray());
        parser.setKind(ASTParser.K_COMPILATION_UNIT);
        CompilationUnit cu = (CompilationUnit) parser.createAST(null);
        org.eclipse.jdt.core.dom.TypeDeclaration type = (org.eclipse.jdt.core.dom.TypeDeclaration) cu.types().get(0);

        Symbol dummy = new Symbol("s", "method", "s", "f", 1, "m", null);

        MethodDeclaration[] methods = type.getMethods();

        // main
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[0], dummy).contains("main_module"));
        // post
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[1], dummy).contains("api_route"));
        // put
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[2], dummy).contains("api_route"));
        // delete
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[3], dummy).contains("api_route"));
        // patch
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[4], dummy).contains("api_route"));
        // req
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[5], dummy).contains("api_route"));
        // path
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[6], dummy).contains("api_route"));
        // paramTest
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[7], dummy).contains("test"));
        // repeatTest
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[8], dummy).contains("test"));
        // factoryTest
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[9], dummy).contains("test"));
        // tearDown
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[10], dummy).contains("test_fixture"));
        // beforeAll
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[11], dummy).contains("test_fixture"));
        // afterAll
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[12], dummy).contains("test_fixture"));
        // init
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[13], dummy).contains("framework_callback"));
        // destroy
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[14], dummy).contains("framework_callback"));
        // onEvent
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[15], dummy).contains("framework_callback"));
        // sched
        assertTrue(JavaEntryPointDetector.detectEntryPoints(methods[16], dummy).contains("task"));
    }
}
