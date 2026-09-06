package com.lineagelens.parser;

import com.lineagelens.model.CodeGraph;
import com.lineagelens.model.Relation;
import com.lineagelens.model.Symbol;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;

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

    @Test
    public void testFieldExtractionWithVisibilityModifiers(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("FieldsClass.java");
        String code = """
                package com.example;

                public class FieldsClass {
                    public String publicField;
                    private int privateField;
                    protected boolean protectedField;
                    String packageField;
                    public static final String CONSTANT = "value";
                    private static int staticCounter = 0;
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify public field
        Symbol publicField = graph.getSymbol("com.example.FieldsClass.publicField");
        assertNotNull(publicField);
        assertEquals("field", publicField.getKind());
        assertEquals("public", publicField.getVisibility());

        // Verify private field
        Symbol privateField = graph.getSymbol("com.example.FieldsClass.privateField");
        assertNotNull(privateField);
        assertEquals("private", privateField.getVisibility());

        // Verify protected field
        Symbol protectedField = graph.getSymbol("com.example.FieldsClass.protectedField");
        assertNotNull(protectedField);
        assertEquals("protected", protectedField.getVisibility());

        // Verify package-private field
        Symbol packageField = graph.getSymbol("com.example.FieldsClass.packageField");
        assertNotNull(packageField);
        assertEquals("package", packageField.getVisibility());

        // Verify static final constant
        Symbol constant = graph.getSymbol("com.example.FieldsClass.CONSTANT");
        assertNotNull(constant);
        assertTrue(constant.isStatic());
        assertTrue(constant.isFinal());

        // Verify static counter
        Symbol counter = graph.getSymbol("com.example.FieldsClass.staticCounter");
        assertNotNull(counter);
        assertTrue(counter.isStatic());
    }

    @Test
    public void testEnumConstantExtraction(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path enumFile = pkgDir.resolve("Status.java");
        String code = """
                package com.example;

                public enum Status {
                    PENDING, ACTIVE, COMPLETED;
                }
                """;
        Files.writeString(enumFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify enum constants
        Symbol pending = graph.getSymbol("com.example.Status.PENDING");
        assertNotNull(pending);
        assertEquals("field", pending.getKind());
        assertEquals("public", pending.getVisibility());
        assertTrue(pending.isStatic());
        assertTrue(pending.isFinal());

        Symbol active = graph.getSymbol("com.example.Status.ACTIVE");
        assertNotNull(active);

        Symbol completed = graph.getSymbol("com.example.Status.COMPLETED");
        assertNotNull(completed);
    }

    @Test
    public void testLocalVariableExtraction(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("LocalsClass.java");
        String code = """
                package com.example;

                public class LocalsClass {
                    public void methodWithLocals() {
                        String localVar = "test";
                        int count = 0;

                        if (count > 0) {
                            String ifLocal = "nested";
                        }

                        for (int i = 0; i < 10; i++) {
                            String forLocal = String.valueOf(i);
                        }

                        for (String s : new String[]{"a", "b"}) {
                            String enhancedForLocal = s;
                        }

                        try {
                            String tryLocal = "in try";
                        } catch (Exception e) {
                            String catchLocal = e.getMessage();
                        }
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol method = graph.getSymbol("com.example.LocalsClass.methodWithLocals");
        assertNotNull(method);

        List<java.util.Map<String, Object>> locals = method.getLocals();
        assertNotNull(locals);
        assertFalse(locals.isEmpty(), "Expected local variables to be extracted");

        // Verify local variable names
        List<String> localNames = locals.stream()
                .map(m -> (String) m.get("name"))
                .toList();
        assertTrue(localNames.contains("localVar"));
        assertTrue(localNames.contains("count"));
    }

    @Test
    public void testNestedClassesAndInnerClasses(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("OuterClass.java");
        String code = """
                package com.example;

                public class OuterClass {
                    public class InnerClass {
                        public void innerMethod() {}
                    }

                    private static class StaticInner {
                        public void staticInnerMethod() {}
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify nested classes are registered
        Symbol outer = graph.getSymbol("com.example.OuterClass");
        assertNotNull(outer);

        Symbol inner = graph.getSymbol("com.example.OuterClass.InnerClass");
        assertNotNull(inner);

        Symbol staticInner = graph.getSymbol("com.example.OuterClass.StaticInner");
        assertNotNull(staticInner);
    }

    @Test
    public void testAnnotationExtraction(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("AnnotatedClass.java");
        String code = """
                package com.example;

                import java.lang.Override;
                import org.springframework.stereotype.Service;

                @Service
                public class AnnotatedClass {
                    @Override
                    public String toString() {
                        return "test";
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol classSymbol = graph.getSymbol("com.example.AnnotatedClass");
        assertNotNull(classSymbol);
        assertFalse(classSymbol.getDecorators().isEmpty());

        Symbol method = graph.getSymbol("com.example.AnnotatedClass.toString");
        assertNotNull(method);
        assertTrue(method.getDecorators().contains("Override"));
    }

    @Test
    public void testImportDeclarationsCreateRelations(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("ImportClass.java");
        String code = """
                package com.example;

                import java.util.List;
                import java.util.*;
                import static java.lang.Math.PI;

                public class ImportClass {
                    public List<String> items;
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify IMPORTS relations exist
        List<Relation> importsRelations = graph.getRelations().stream()
                .filter(r -> "IMPORTS".equals(r.getKind()))
                .toList();
        assertFalse(importsRelations.isEmpty(), "Expected IMPORTS relations from import statements");
    }

    @Test
    public void testEmptyProjectAnalysis(@TempDir Path tempDir) throws IOException {
        // Create directories but no Java files
        Files.createDirectories(tempDir.resolve("src/main/java/com/example"));

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        assertNotNull(graph);
        assertTrue(graph.getSymbols().isEmpty());
    }

    @Test
    public void testMethodParametersExtraction(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("MethodClass.java");
        String code = """
                package com.example;

                public class MethodClass {
                    public void methodWithParams(String name, int age, boolean active) {
                        System.out.println(name);
                    }

                    public int add(int a, int b) {
                        return a + b;
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol method = graph.getSymbol("com.example.MethodClass.methodWithParams");
        assertNotNull(method);
        assertFalse(method.getInputs().isEmpty());
    }

    @Test
    public void testReturnTypeExtraction(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("ReturnClass.java");
        String code = """
                package com.example;

                public class ReturnClass {
                    public String getName() {
                        return "test";
                    }

                    public int getCount() {
                        return 0;
                    }

                    public void doNothing() {
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol getNameMethod = graph.getSymbol("com.example.ReturnClass.getName");
        assertNotNull(getNameMethod);
        assertFalse(getNameMethod.getOutputs().isEmpty());

        Symbol getCountMethod = graph.getSymbol("com.example.ReturnClass.getCount");
        assertNotNull(getCountMethod);
        assertFalse(getCountMethod.getOutputs().isEmpty());

        Symbol doNothingMethod = graph.getSymbol("com.example.ReturnClass.doNothing");
        assertNotNull(doNothingMethod);
        assertFalse(doNothingMethod.getOutputs().isEmpty());
    }

    @Test
    public void testConstructorDetection(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("ConstructorClass.java");
        String code = """
                package com.example;

                public class ConstructorClass {
                    private String name;

                    public ConstructorClass(String name) {
                        this.name = name;
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol constructor = graph.getSymbol("com.example.ConstructorClass.ConstructorClass");
        assertNotNull(constructor);
        assertEquals("constructor", constructor.getKind());
    }

    @Test
    public void testAbstractClassDetection(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("AbstractClass.java");
        String code = """
                package com.example;

                public abstract class AbstractClass {
                    public abstract void abstractMethod();
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol abstractClass = graph.getSymbol("com.example.AbstractClass");
        assertNotNull(abstractClass);
        assertTrue(abstractClass.isAbstract());

        Symbol abstractMethod = graph.getSymbol("com.example.AbstractClass.abstractMethod");
        assertNotNull(abstractMethod);
        assertTrue(abstractMethod.isAbstract());
    }

    @Test
    public void testPackageContainerHierarchy(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example/nested/deep");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("DeepClass.java");
        String code = """
                package com.example.nested.deep;

                public class DeepClass {
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify package containers are created hierarchically
        assertNotNull(graph.getContainer("com"));
        assertNotNull(graph.getContainer("com.example"));
        assertNotNull(graph.getContainer("com.example.nested"));
        assertNotNull(graph.getContainer("com.example.nested.deep"));
    }

    @Test
    public void testJavadocExtraction(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("DocumentedClass.java");
        String code = """
                package com.example;

                /**
                 * This is a documented class.
                 */
                public class DocumentedClass {
                    /**
                     * This field is important.
                     */
                    public String field;

                    /**
                     * This method does something.
                     */
                    public void documentedMethod() {
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol classSymbol = graph.getSymbol("com.example.DocumentedClass");
        assertNotNull(classSymbol);
        assertNotNull(classSymbol.getDescription());
        assertFalse(classSymbol.getDescription().isEmpty());

        Symbol field = graph.getSymbol("com.example.DocumentedClass.field");
        assertNotNull(field);
        assertNotNull(field.getDescription());

        Symbol method = graph.getSymbol("com.example.DocumentedClass.documentedMethod");
        assertNotNull(method);
        assertNotNull(method.getDescription());
    }

    @Test
    public void testMixedBlockStructures(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("BlocksClass.java");
        String code = """
                package com.example;

                public class BlocksClass {
                    public void complexMethod() {
                        // Simple block
                        {
                            String nestedBlock = "test";
                        }

                        // While loop
                        while (true) {
                            String whileVar = "loop";
                            break;
                        }

                        // Do-while loop
                        do {
                            String doVar = "doloop";
                        } while (false);
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol method = graph.getSymbol("com.example.BlocksClass.complexMethod");
        assertNotNull(method);
        List<java.util.Map<String, Object>> locals = method.getLocals();
        assertNotNull(locals);
        assertFalse(locals.isEmpty());
    }

    @Test
    public void testMultipleMethodOverloads(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("OverloadClass.java");
        String code = """
                package com.example;

                public class OverloadClass {
                    public void process(String name) {
                        System.out.println(name);
                    }

                    public void process(int value) {
                        System.out.println(value);
                    }

                    public void process(String name, int value) {
                        System.out.println(name + value);
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // All overloaded methods should be registered
        Symbol processMethod1 = graph.getSymbol("com.example.OverloadClass.process");
        assertNotNull(processMethod1, "Expected process method to be registered");
    }

    @Test
    public void testInterfaceImplementation(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path interfaceFile = pkgDir.resolve("MyInterface.java");
        String interfaceCode = """
                package com.example;

                public interface MyInterface {
                    void doSomething();
                    String getName();
                }
                """;
        Files.writeString(interfaceFile, interfaceCode);

        Path implFile = pkgDir.resolve("MyImpl.java");
        String implCode = """
                package com.example;

                public class MyImpl implements MyInterface {
                    @Override
                    public void doSomething() {
                    }

                    @Override
                    public String getName() {
                        return "impl";
                    }
                }
                """;
        Files.writeString(implFile, implCode);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol intfSymbol = graph.getSymbol("com.example.MyInterface");
        assertNotNull(intfSymbol);
        assertEquals("interface", intfSymbol.getKind());

        Symbol implSymbol = graph.getSymbol("com.example.MyImpl");
        assertNotNull(implSymbol);
        assertTrue(implSymbol.getBases().contains("MyInterface"));
    }

    @Test
    public void testComplexInheritanceHierarchy(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path baseFile = pkgDir.resolve("BaseClass.java");
        String baseCode = """
                package com.example;

                public class BaseClass {
                    public void baseMethod() {}
                }
                """;
        Files.writeString(baseFile, baseCode);

        Path midFile = pkgDir.resolve("MidClass.java");
        String midCode = """
                package com.example;

                public class MidClass extends BaseClass {
                    public void midMethod() {
                        baseMethod();
                    }
                }
                """;
        Files.writeString(midFile, midCode);

        Path leafFile = pkgDir.resolve("LeafClass.java");
        String leafCode = """
                package com.example;

                public class LeafClass extends MidClass {
                    public void leafMethod() {
                        midMethod();
                    }
                }
                """;
        Files.writeString(leafFile, leafCode);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify class hierarchy
        Symbol leaf = graph.getSymbol("com.example.LeafClass");
        assertTrue(leaf.getBases().contains("MidClass"));

        Symbol mid = graph.getSymbol("com.example.MidClass");
        assertTrue(mid.getBases().contains("BaseClass"));

        // Verify INHERITS relations
        boolean hasLeafInherits = graph.getRelations().stream().anyMatch(r ->
                r.getSource().equals("com.example.LeafClass") &&
                r.getTarget().equals("com.example.MidClass") &&
                "INHERITS".equals(r.getKind())
        );
        assertTrue(hasLeafInherits);
    }

    @Test
    public void testMultipleFiles(@TempDir Path tempDir) throws IOException {
        // Create multiple Java files to ensure batch processing works
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        for (int i = 0; i < 5; i++) {
            Path classFile = pkgDir.resolve("Class" + i + ".java");
            String code = "package com.example;\n"
                    + "public class Class" + i + " {\n"
                    + "    public void method() {}\n"
                    + "}\n";
            Files.writeString(classFile, code);
        }

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify all files were processed
        for (int i = 0; i < 5; i++) {
            Symbol classSymbol = graph.getSymbol("com.example.Class" + i);
            assertNotNull(classSymbol, "Expected class Class" + i + " to be in graph");
        }
    }

    @Test
    public void testMavenProjectStructure(@TempDir Path tempDir) throws IOException {
        // Create Maven-style project structure
        Path srcMainJava = tempDir.resolve("src/main/java/com/example");
        Path srcTestJava = tempDir.resolve("src/test/java/com/example");
        Files.createDirectories(srcMainJava);
        Files.createDirectories(srcTestJava);

        // Create pom.xml
        Path pomFile = tempDir.resolve("pom.xml");
        String pomContent = """
                <project>
                    <groupId>com.example</groupId>
                    <artifactId>test-app</artifactId>
                    <version>1.0.0</version>
                </project>
                """;
        Files.writeString(pomFile, pomContent);

        // Create source file
        Path classFile = srcMainJava.resolve("App.java");
        String classCode = """
                package com.example;

                public class App {
                    public void run() {}
                }
                """;
        Files.writeString(classFile, classCode);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol appClass = graph.getSymbol("com.example.App");
        assertNotNull(appClass);
    }

    @Test
    public void testGenericsAndTypeParameters(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("GenericClass.java");
        String code = """
                package com.example;

                import java.util.List;

                public class GenericClass<T> {
                    private T value;

                    public GenericClass(T value) {
                        this.value = value;
                    }

                    public T getValue() {
                        return value;
                    }

                    public <U> U convert(U input) {
                        return input;
                    }

                    public List<T> toList() {
                        return List.of();
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol classSymbol = graph.getSymbol("com.example.GenericClass");
        assertNotNull(classSymbol);
        assertEquals("class", classSymbol.getKind());

        Symbol constructor = graph.getSymbol("com.example.GenericClass.GenericClass");
        assertNotNull(constructor);
    }

    @Test
    public void testSingleMemberAndNormalAnnotations(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("AnnotatedMethods.java");
        String code = """
                package com.example;

                import java.lang.Deprecated;

                public class AnnotatedMethods {
                    @Deprecated("Use newMethod instead")
                    public void oldMethod() {}

                    @Deprecated
                    public void anotherOldMethod() {}

                    @SuppressWarnings("unchecked")
                    public void suppressedMethod() {}
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol oldMethod = graph.getSymbol("com.example.AnnotatedMethods.oldMethod");
        assertNotNull(oldMethod);
        assertTrue(oldMethod.getDecorators().contains("Deprecated"));

        // Verify DECORATES relations exist
        List<Relation> decorates = graph.getRelations().stream()
                .filter(r -> "DECORATES".equals(r.getKind()))
                .toList();
        assertFalse(decorates.isEmpty(), "Expected DECORATES relations for annotations");
    }

    @Test
    public void testMultiplePackagesInSingleFile(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("MultiClass.java");
        String code = """
                package com.example;

                public class PublicClass {
                    public void publicMethod() {}
                }

                class PackageClass {
                    void packageMethod() {}
                }

                abstract class AbstractHelper {
                    abstract void help();
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // All classes in the file should be registered
        Symbol publicClass = graph.getSymbol("com.example.PublicClass");
        assertNotNull(publicClass);

        Symbol packageClass = graph.getSymbol("com.example.PackageClass");
        assertNotNull(packageClass);

        Symbol abstractClass = graph.getSymbol("com.example.AbstractHelper");
        assertNotNull(abstractClass);
    }

    @Test
    public void testNoPackageDeclaration(@TempDir Path tempDir) throws IOException {
        Path dir = tempDir.resolve("src/main/java");
        Files.createDirectories(dir);

        Path classFile = dir.resolve("DefaultPackageClass.java");
        String code = """
                // No package declaration - default package
                public class DefaultPackageClass {
                    public void method() {}
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Should still register the class even without explicit package
        Symbol classSymbol = graph.getSymbol("DefaultPackageClass");
        assertNotNull(classSymbol);
    }

    @Test
    public void testMethodsCallingOtherMethods(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("Calculator.java");
        String code = """
                package com.example;

                public class Calculator {
                    public int add(int a, int b) {
                        return a + b;
                    }

                    public int multiply(int a, int b) {
                        return a * b;
                    }

                    public int calculate() {
                        int sum = add(5, 3);
                        int product = multiply(sum, 2);
                        return product;
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol calculate = graph.getSymbol("com.example.Calculator.calculate");
        assertNotNull(calculate);

        // Verify CALLS relations exist
        List<Relation> callsFromCalculate = graph.getRelations().stream()
                .filter(r -> r.getSource().equals("com.example.Calculator.calculate") &&
                        ("CALLS".equals(r.getKind()) || "AWAIT_CALLS".equals(r.getKind())))
                .toList();
        assertFalse(callsFromCalculate.isEmpty(), "Expected CALLS relations from calculate method");
    }

    @Test
    public void testEnumWithMethods(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path enumFile = pkgDir.resolve("Color.java");
        String code = """
                package com.example;

                public enum Color {
                    RED(255, 0, 0),
                    GREEN(0, 255, 0),
                    BLUE(0, 0, 255);

                    private final int r, g, b;

                    Color(int r, int g, int b) {
                        this.r = r;
                        this.g = g;
                        this.b = b;
                    }

                    public int getRed() {
                        return r;
                    }
                }
                """;
        Files.writeString(enumFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol enumSymbol = graph.getSymbol("com.example.Color");
        assertNotNull(enumSymbol);
        assertEquals("enum", enumSymbol.getKind());

        Symbol enumField = graph.getSymbol("com.example.Color.RED");
        assertNotNull(enumField);
    }

    @Test
    public void testStaticInitializers(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("StaticClass.java");
        String code = """
                package com.example;

                public class StaticClass {
                    public static final int CONSTANT;

                    static {
                        CONSTANT = 42;
                    }

                    public static void staticMethod() {
                        System.out.println(CONSTANT);
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol staticMethod = graph.getSymbol("com.example.StaticClass.staticMethod");
        assertNotNull(staticMethod);

        Symbol constant = graph.getSymbol("com.example.StaticClass.CONSTANT");
        assertNotNull(constant);
        assertTrue(constant.isStatic());
        assertTrue(constant.isFinal());
    }

    @Test
    public void testBuildTargetDirectoriesFiltered(@TempDir Path tempDir) throws IOException {
        // Create Java files in both src and build directories
        Path srcDir = tempDir.resolve("src/main/java/com/example");
        Path buildDir = tempDir.resolve("build/classes/java");
        Path targetDir = tempDir.resolve("target/classes");

        Files.createDirectories(srcDir);
        Files.createDirectories(buildDir);
        Files.createDirectories(targetDir);

        // File in src (should be analyzed)
        Path srcFile = srcDir.resolve("SourceClass.java");
        String srcCode = """
                package com.example;
                public class SourceClass {}
                """;
        Files.writeString(srcFile, srcCode);

        // File in build (should be filtered out)
        Path buildFile = buildDir.resolve("BuildClass.java");
        Files.writeString(buildFile, "public class BuildClass {}");

        // File in target (should be filtered out)
        Path targetFile = targetDir.resolve("TargetClass.java");
        Files.writeString(targetFile, "public class TargetClass {}");

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Only source class should be in the graph
        Symbol sourceClass = graph.getSymbol("com.example.SourceClass");
        assertNotNull(sourceClass);

        Symbol buildClass = graph.getSymbol("BuildClass");
        assertNull(buildClass, "Build directory files should be filtered out");

        Symbol targetClass = graph.getSymbol("TargetClass");
        assertNull(targetClass, "Target directory files should be filtered out");
    }

    @Test
    public void testProjectWithGradleLayout(@TempDir Path tempDir) throws IOException {
        // Create Gradle-style project structure
        Path srcDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(srcDir);

        Path buildGradleFile = tempDir.resolve("build.gradle");
        Files.writeString(buildGradleFile, "plugins { id 'java' }");

        Path classFile = srcDir.resolve("GradleApp.java");
        String code = """
                package com.example;
                public class GradleApp {
                    public void run() {}
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol appClass = graph.getSymbol("com.example.GradleApp");
        assertNotNull(appClass);
    }

    @Test
    public void testProjectWithMultipleSourceRoots(@TempDir Path tempDir) throws IOException {
        // Create multiple source directories
        Path mainSrc = tempDir.resolve("src/main/java/com/example");
        Path testSrc = tempDir.resolve("src/test/java/com/example");

        Files.createDirectories(mainSrc);
        Files.createDirectories(testSrc);

        // Main source file
        Path mainFile = mainSrc.resolve("Main.java");
        String mainCode = """
                package com.example;
                public class Main {}
                """;
        Files.writeString(mainFile, mainCode);

        // Test source file
        Path testFile = testSrc.resolve("MainTest.java");
        String testCode = """
                package com.example;
                public class MainTest {}
                """;
        Files.writeString(testFile, testCode);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol mainClass = graph.getSymbol("com.example.Main");
        assertNotNull(mainClass);

        Symbol testClass = graph.getSymbol("com.example.MainTest");
        assertNotNull(testClass);
    }

    @Test
    public void testPomXmlWithoutModules(@TempDir Path tempDir) throws IOException {
        Path srcDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(srcDir);

        // Create a simple pom.xml without modules
        Path pomFile = tempDir.resolve("pom.xml");
        String pomContent = """
                <project>
                    <groupId>com.example</groupId>
                    <artifactId>simple-app</artifactId>
                    <version>1.0.0</version>
                </project>
                """;
        Files.writeString(pomFile, pomContent);

        Path classFile = srcDir.resolve("SimpleApp.java");
        String code = """
                package com.example;
                public class SimpleApp {}
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol appClass = graph.getSymbol("com.example.SimpleApp");
        assertNotNull(appClass);
    }

    @Test
    public void testVariableTypeResolution(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("TypesClass.java");
        String code = """
                package com.example;

                import java.util.List;
                import java.util.ArrayList;

                public class TypesClass {
                    public void testTypes() {
                        String str = "test";
                        int num = 42;
                        List<String> list = new ArrayList<>();
                        boolean flag = true;
                        double pi = 3.14;
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol method = graph.getSymbol("com.example.TypesClass.testTypes");
        assertNotNull(method);

        List<java.util.Map<String, Object>> locals = method.getLocals();
        assertNotNull(locals);
        assertFalse(locals.isEmpty(), "Expected local variables to be extracted");
    }

    @Test
    public void testMethodWithDefaultReturnType(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("ReturnTypes.java");
        String code = """
                package com.example;

                public class ReturnTypes {
                    public String getName() { return "test"; }
                    public int getCount() { return 0; }
                    public boolean isValid() { return true; }
                    public long getValue() { return 0L; }
                    public double getDouble() { return 0.0; }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol getName = graph.getSymbol("com.example.ReturnTypes.getName");
        assertNotNull(getName);
        assertFalse(getName.getOutputs().isEmpty());

        Symbol getCount = graph.getSymbol("com.example.ReturnTypes.getCount");
        assertNotNull(getCount);
    }

    @Test
    public void testNestedPackageStructure(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example/app/service/impl");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("ServiceImpl.java");
        String code = """
                package com.example.app.service.impl;

                public class ServiceImpl {
                    public void doService() {}
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify nested containers exist
        assertNotNull(graph.getContainer("com"));
        assertNotNull(graph.getContainer("com.example"));
        assertNotNull(graph.getContainer("com.example.app"));
        assertNotNull(graph.getContainer("com.example.app.service"));
        assertNotNull(graph.getContainer("com.example.app.service.impl"));

        Symbol serviceImpl = graph.getSymbol("com.example.app.service.impl.ServiceImpl");
        assertNotNull(serviceImpl);
    }

    @Test
    public void testOverrideAnnotationAndRelation(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path baseFile = pkgDir.resolve("Base.java");
        String baseCode = """
                package com.example;
                public class Base {
                    public void method() {}
                }
                """;
        Files.writeString(baseFile, baseCode);

        Path derivedFile = pkgDir.resolve("Derived.java");
        String derivedCode = """
                package com.example;
                public class Derived extends Base {
                    @Override
                    public void method() {}
                }
                """;
        Files.writeString(derivedFile, derivedCode);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol derivedMethod = graph.getSymbol("com.example.Derived.method");
        assertNotNull(derivedMethod);
        assertTrue(derivedMethod.getDecorators().contains("Override"));

        // Verify OVERRIDES relation exists
        boolean hasOverrides = graph.getRelations().stream().anyMatch(r ->
                r.getSource().equals("com.example.Derived.method") &&
                r.getTarget().equals("com.example.Base.method") &&
                "OVERRIDES".equals(r.getKind())
        );
        assertTrue(hasOverrides, "Expected OVERRIDES relation");
    }

    @Test
    public void testComplexAnnotationValues(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("AnnotatedData.java");
        String code = """
                package com.example;

                public class AnnotatedData {
                    @Deprecated(since = "1.0", forRemoval = true)
                    public void oldMethod() {}

                    @SuppressWarnings({"unchecked", "rawtypes"})
                    public void suppressedMethod() {}
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol oldMethod = graph.getSymbol("com.example.AnnotatedData.oldMethod");
        assertNotNull(oldMethod);

        Symbol suppressedMethod = graph.getSymbol("com.example.AnnotatedData.suppressedMethod");
        assertNotNull(suppressedMethod);
    }

    @Test
    public void testFieldInitializationTypes(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("FieldInit.java");
        String code = """
                package com.example;

                import java.util.ArrayList;
                import java.util.HashMap;
                import java.util.List;
                import java.util.Map;

                public class FieldInit {
                    public String strField = "default";
                    public List<String> listField = new ArrayList<>();
                    public Map<String, Integer> mapField = new HashMap<>();
                    public int[] arrayField = new int[10];
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol strField = graph.getSymbol("com.example.FieldInit.strField");
        assertNotNull(strField);
        assertEquals("field", strField.getKind());

        Symbol listField = graph.getSymbol("com.example.FieldInit.listField");
        assertNotNull(listField);

        Symbol mapField = graph.getSymbol("com.example.FieldInit.mapField");
        assertNotNull(mapField);

        Symbol arrayField = graph.getSymbol("com.example.FieldInit.arrayField");
        assertNotNull(arrayField);
    }

    @Test
    public void testIfElseBlocks(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("IfElseClass.java");
        String code = """
                package com.example;

                public class IfElseClass {
                    public void checkCondition(int value) {
                        if (value > 0) {
                            String positive = "positive";
                        } else if (value < 0) {
                            String negative = "negative";
                        } else {
                            String zero = "zero";
                        }
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol method = graph.getSymbol("com.example.IfElseClass.checkCondition");
        assertNotNull(method);

        List<java.util.Map<String, Object>> locals = method.getLocals();
        assertNotNull(locals);
        // Should capture variables from all branches
        assertFalse(locals.isEmpty());
    }

    @Test
    public void testNonExistentRootDirectory() throws IOException {
        Path nonExistentPath = Paths.get("/tmp/non_existent_project_" + System.nanoTime());
        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(nonExistentPath);
        CodeGraph graph = analyzer.analyze();

        assertNotNull(graph);
        // Should handle non-existent paths gracefully
        assertTrue(graph.getSymbols().isEmpty());
    }

    @Test
    public void testPomXmlWithModules(@TempDir Path tempDir) throws IOException {
        // Create a multi-module Maven project structure
        Path srcDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(srcDir);

        // Create pom.xml with modules
        Path pomFile = tempDir.resolve("pom.xml");
        String pomContent = """
                <project>
                    <groupId>com.example</groupId>
                    <artifactId>parent</artifactId>
                    <version>1.0.0</version>
                    <modules>
                        <module>module1</module>
                        <module>module2</module>
                    </modules>
                </project>
                """;
        Files.writeString(pomFile, pomContent);

        // Create module1 structure
        Path module1 = tempDir.resolve("module1/src/main/java/com/example");
        Files.createDirectories(module1);
        Path module1File = module1.resolve("Module1.java");
        Files.writeString(module1File, "package com.example; public class Module1 {}");

        Path classFile = srcDir.resolve("MainApp.java");
        String code = """
                package com.example;
                public class MainApp {}
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol mainApp = graph.getSymbol("com.example.MainApp");
        assertNotNull(mainApp);
    }

    @Test
    public void testLargeNumberOfFiles(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        // Create a larger number of files to stress the parser
        for (int i = 0; i < 10; i++) {
            Path classFile = pkgDir.resolve("LargeClass" + i + ".java");
            String code = "package com.example;\n"
                    + "public class LargeClass" + i + " {\n"
                    + "    public void method" + i + "() {}\n"
                    + "}\n";
            Files.writeString(classFile, code);
        }

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify all classes were parsed
        for (int i = 0; i < 10; i++) {
            Symbol cls = graph.getSymbol("com.example.LargeClass" + i);
            assertNotNull(cls, "Expected LargeClass" + i + " to be parsed");
        }
    }

    @Test
    public void testComplexCallChain(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("CallChain.java");
        String code = """
                package com.example;

                public class CallChain {
                    public void methodA() {
                        methodB();
                    }

                    public void methodB() {
                        methodC();
                    }

                    public void methodC() {
                        methodD();
                    }

                    public void methodD() {
                        System.out.println("end");
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        // Verify all methods are registered
        Symbol methodA = graph.getSymbol("com.example.CallChain.methodA");
        assertNotNull(methodA);

        Symbol methodD = graph.getSymbol("com.example.CallChain.methodD");
        assertNotNull(methodD);

        // Verify CALLS relations exist
        List<Relation> calls = graph.getRelations().stream()
                .filter(r -> r.getKind().equals("CALLS") || r.getKind().equals("AWAIT_CALLS"))
                .toList();
        assertFalse(calls.isEmpty(), "Expected CALLS relations in call chain");
    }

    @Test
    public void testMultipleInterfaces(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("MultiInterface.java");
        String code = """
                package com.example;

                interface Interface1 {
                    void method1();
                }

                interface Interface2 {
                    void method2();
                }

                interface Interface3 extends Interface1 {
                    void method3();
                }

                public class MultiImpl implements Interface1, Interface2, Interface3 {
                    @Override
                    public void method1() {}

                    @Override
                    public void method2() {}

                    @Override
                    public void method3() {}
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol multiImpl = graph.getSymbol("com.example.MultiImpl");
        assertNotNull(multiImpl);
        assertTrue(multiImpl.getBases().size() >= 3);
    }

    @Test
    public void testExceptionHandlingInComplex(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("ExceptionClass.java");
        String code = """
                package com.example;

                public class ExceptionClass {
                    public void handleExceptions() {
                        try {
                            String data = "test";
                            int x = Integer.parseInt(data);
                        } catch (NumberFormatException e) {
                            String errorMsg = e.getMessage();
                        } catch (Exception e) {
                            String genericError = e.toString();
                        } finally {
                            String cleanup = "cleanup";
                        }
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol method = graph.getSymbol("com.example.ExceptionClass.handleExceptions");
        assertNotNull(method);

        List<java.util.Map<String, Object>> locals = method.getLocals();
        assertNotNull(locals);
        assertFalse(locals.isEmpty(), "Expected to capture variables from try/catch/finally blocks");
    }

    @Test
    public void testClassWithMultipleConstructors(@TempDir Path tempDir) throws IOException {
        Path pkgDir = tempDir.resolve("src/main/java/com/example");
        Files.createDirectories(pkgDir);

        Path classFile = pkgDir.resolve("MultiConstructor.java");
        String code = """
                package com.example;

                public class MultiConstructor {
                    private String name;
                    private int age;

                    public MultiConstructor() {
                        this("Unknown", 0);
                    }

                    public MultiConstructor(String name) {
                        this(name, 0);
                    }

                    public MultiConstructor(String name, int age) {
                        this.name = name;
                        this.age = age;
                    }
                }
                """;
        Files.writeString(classFile, code);

        JavaAstAnalyzer analyzer = new JavaAstAnalyzer(tempDir);
        CodeGraph graph = analyzer.analyze();

        Symbol constructor1 = graph.getSymbol("com.example.MultiConstructor.MultiConstructor");
        assertNotNull(constructor1);
        assertEquals("constructor", constructor1.getKind());

        Symbol classSymbol = graph.getSymbol("com.example.MultiConstructor");
        assertNotNull(classSymbol);
        assertEquals("class", classSymbol.getKind());
    }
}
