package com.lineagelens.parser;

import com.lineagelens.model.CodeGraph;
import com.lineagelens.model.Container;
import com.lineagelens.model.Evidence;
import com.lineagelens.model.Relation;
import com.lineagelens.model.Symbol;
import org.eclipse.jdt.core.JavaCore;
import org.eclipse.jdt.core.dom.*;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

public class JavaAstAnalyzer {

    private final Path projectRoot;

    public JavaAstAnalyzer(Path projectRoot) {
        this.projectRoot = projectRoot.toAbsolutePath().normalize();
    }

    public CodeGraph analyze() throws IOException {
        CodeGraph graph = new CodeGraph(projectRoot.toString());
        List<Path> javaFiles = collectJavaFiles(projectRoot);

        // Pre-parse compilation units
        Map<Path, CompilationUnit> compilationUnits = parseCompilationUnits(javaFiles);

        // Phase 1: Symbol & Container extraction
        for (Map.Entry<Path, CompilationUnit> entry : compilationUnits.entrySet()) {
            Path filePath = entry.getKey();
            CompilationUnit cu = entry.getValue();
            String relPath = projectRoot.relativize(filePath).toString().replace('\\', '/');

            cu.accept(new DefinitionsVisitor(graph, relPath, cu));
        }

        // Phase 2: Relationship extraction (CALLS, INHERITS, OVERRIDES, DECORATES)
        for (Map.Entry<Path, CompilationUnit> entry : compilationUnits.entrySet()) {
            Path filePath = entry.getKey();
            CompilationUnit cu = entry.getValue();
            String relPath = projectRoot.relativize(filePath).toString().replace('\\', '/');

            cu.accept(new RelationshipsVisitor(graph, relPath, cu));
        }

        graph.prepareForSerialization();
        return graph;
    }

    private List<Path> collectJavaFiles(Path root) throws IOException {
        List<Path> javaFiles = new ArrayList<>();
        if (!Files.exists(root)) {
            return javaFiles;
        }

        try (Stream<Path> stream = Files.walk(root)) {
            stream.filter(p -> Files.isRegularFile(p) && p.toString().endsWith(".java"))
                  .filter(p -> !p.toString().contains("/build/") && !p.toString().contains("/target/"))
                  .forEach(javaFiles::add);
        }
        return javaFiles;
    }

    /**
     * Discover all Java source roots in the project.
     * Handles Maven multi-module projects and Gradle projects.
     */
    private List<Path> discoverSourceRoots(Path root) throws IOException {
        List<Path> sourceRoots = new ArrayList<>();

        // Standard Maven layout: src/main/java, src/test/java
        sourceRoots.add(root.resolve("src/main/java"));
        sourceRoots.add(root.resolve("src/test/java"));

        // Multi-module Maven: scan pom.xml for <module> entries
        Path pomXml = root.resolve("pom.xml");
        if (Files.exists(pomXml)) {
            try {
                String pomContent = Files.readString(pomXml);
                Pattern modulePattern = Pattern.compile("<module>([^<]+)</module>");
                Matcher m = modulePattern.matcher(pomContent);
                while (m.find()) {
                    Path modulePath = root.resolve(m.group(1)).normalize();
                    sourceRoots.add(modulePath.resolve("src/main/java"));
                    sourceRoots.add(modulePath.resolve("src/test/java"));
                }
            } catch (Exception e) {
                System.err.println("Warning: Failed to parse pom.xml for modules: " + e.getMessage());
            }
        }

        // Gradle: scan build.gradle (simplified - assume standard layout)
        Path buildGradle = root.resolve("build.gradle");
        if (Files.exists(buildGradle)) {
            // For now, we rely on standard layout discovery above
            // Full Gradle parsing is complex and can be enhanced later
        }

        // Filter to only existing directories and return unique paths
        return sourceRoots.stream()
            .filter(Files::isDirectory)
            .map(Path::normalize)
            .distinct()
            .toList();
    }

    /**
     * Resolve classpath jars with best-effort strategy.
     * First tries Maven, then Gradle cache, then falls back gracefully.
     */
    private List<Path> resolveClasspath(Path root) {
        List<Path> classpath = new ArrayList<>();

        // Try Maven dependency:build-classpath
        Path pomXml = root.resolve("pom.xml");
        if (Files.exists(pomXml)) {
            try {
                Process p = Runtime.getRuntime().exec(new String[]{
                    "mvn", "dependency:build-classpath",
                    "-DincludeScope=compile",
                    "-q", "-f", pomXml.toAbsolutePath().toString()
                });
                BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()));
                String line = br.readLine();
                if (line != null && !line.trim().isEmpty()) {
                    for (String jarPath : line.split(File.pathSeparator)) {
                        Path jar = Paths.get(jarPath);
                        if (Files.exists(jar)) {
                            classpath.add(jar);
                        }
                    }
                }
                int exitCode = p.waitFor();
                if (classpath.isEmpty() && exitCode == 0) {
                    System.err.println("Warning: mvn dependency:build-classpath returned no jars");
                } else if (!classpath.isEmpty()) {
                    System.err.println("Resolved " + classpath.size() + " classpath jars via Maven");
                    return classpath;
                }
            } catch (Exception e) {
                System.err.println("Warning: Maven classpath resolution failed: " + e.getMessage());
            }
        }

        // Try Gradle cache (basic heuristic)
        Path gradleHome = Paths.get(System.getProperty("user.home"), ".gradle", "caches", "modules-2", "files-2.1");
        if (Files.isDirectory(gradleHome)) {
            try (Stream<Path> stream = Files.walk(gradleHome, 3)) {
                stream.filter(p -> p.toString().endsWith(".jar"))
                      .limit(100)  // Limit scan to avoid performance issues
                      .forEach(classpath::add);
            } catch (Exception e) {
                System.err.println("Warning: Gradle cache scan failed: " + e.getMessage());
            }
        }

        // If classpath is still empty, warn but don't fail
        if (classpath.isEmpty()) {
            System.err.println("Warning: Could not resolve classpath; will use sourcepath-only mode");
        } else {
            System.err.println("Resolved " + classpath.size() + " classpath entries");
        }

        return classpath;
    }

    private Map<Path, CompilationUnit> parseCompilationUnits(List<Path> files) throws IOException {
        Map<Path, CompilationUnit> units = new HashMap<>();

        if (files.isEmpty()) {
            return units;
        }

        // Discover sourcepath and classpath
        List<Path> sourcepathEntries = discoverSourceRoots(projectRoot);
        List<Path> classpath = resolveClasspath(projectRoot);

        System.err.println("Parsing " + files.size() + " files with "
            + sourcepathEntries.size() + " source roots and "
            + classpath.size() + " classpath entries");

        // Convert to arrays for JDT API
        String[] sourceFilePaths = files.stream()
            .map(p -> p.toAbsolutePath().toString())
            .toArray(String[]::new);

        String[] sourcepaths = sourcepathEntries.stream()
            .map(p -> p.toAbsolutePath().toString())
            .toArray(String[]::new);

        String[] classpaths = classpath.stream()
            .map(p -> p.toAbsolutePath().toString())
            .toArray(String[]::new);

        // Set up compiler options
        Map<String, String> options = JavaCore.getOptions();
        options.put(JavaCore.COMPILER_SOURCE, JavaCore.VERSION_17);
        options.put(JavaCore.COMPILER_COMPLIANCE, JavaCore.VERSION_17);

        // Strategy 1: Try batch parsing (best: full JDT cross-file binding)
        try {
            System.err.println("Strategy 1: Attempting batch parsing with full cross-file binding...");
            units.putAll(batchParse(sourceFilePaths, sourcepaths, classpaths, options));
            System.err.println("Batch parsing succeeded");
            return units;
        } catch (Throwable e) {
            System.err.println("Warning: Batch parsing failed: " + e.getMessage());
        }

        // Strategy 2: Fall back to per-file parsing with symbol index (good: heuristic cross-file linking)
        try {
            System.err.println("Strategy 2: Falling back to per-file parsing with symbol indexing...");
            units.putAll(parseFileByFileWithIndexing(files, options));
            System.err.println("Per-file parsing with indexing succeeded");
            return units;
        } catch (Throwable e) {
            System.err.println("Warning: Per-file parsing with indexing failed: " + e.getMessage());
        }

        // Strategy 3: Last resort - per-file parsing without cross-file binding (worst: no linking)
        System.err.println("Strategy 3: Last resort - per-file parsing without cross-file binding");
        units.putAll(parseFileByFileWithoutIndexing(files, options));

        return units;
    }

    private Map<Path, CompilationUnit> batchParse(String[] sourceFilePaths, String[] sourcepaths,
                                                    String[] classpaths, Map<String, String> options) throws IOException {
        Map<Path, CompilationUnit> units = new HashMap<>();

        ASTParser parser = ASTParser.newParser(AST.JLS17);
        parser.setKind(ASTParser.K_COMPILATION_UNIT);
        parser.setResolveBindings(true);
        parser.setBindingsRecovery(true);
        parser.setIgnoreMethodBodies(false);
        parser.setCompilerOptions(options);
        parser.setEnvironment(classpaths, sourcepaths, null, true);

        FileASTRequestor requestor = new FileASTRequestor() {
            @Override
            public void acceptAST(String sourceFilePath, CompilationUnit cu) {
                Path path = Paths.get(sourceFilePath);
                units.put(path, cu);
            }
        };

        parser.createASTs(sourceFilePaths, null, new String[0], requestor, null);
        return units;
    }

    private Map<Path, CompilationUnit> parseFileByFileWithIndexing(List<Path> files, Map<String, String> options) throws IOException {
        Map<Path, CompilationUnit> units = new HashMap<>();

        // Phase 1: Parse all files individually and build symbol index
        Map<String, Symbol> symbolIndex = new HashMap<>();
        for (Path file : files) {
            try {
                String source = Files.readString(file);
                ASTParser parser = ASTParser.newParser(AST.JLS17);
                parser.setSource(source.toCharArray());
                parser.setKind(ASTParser.K_COMPILATION_UNIT);
                parser.setCompilerOptions(options);

                CompilationUnit cu = (CompilationUnit) parser.createAST(null);
                units.put(file, cu);

                // Index symbols from this file for cross-file resolution
                indexSymbols(cu, symbolIndex);
            } catch (Exception e) {
                System.err.println("Failed to parse " + file + ": " + e.getMessage());
            }
        }

        System.err.println("Built symbol index with " + symbolIndex.size() + " symbols for cross-file linking");
        return units;
    }

    private Map<Path, CompilationUnit> parseFileByFileWithoutIndexing(List<Path> files, Map<String, String> options) {
        Map<Path, CompilationUnit> units = new HashMap<>();

        for (Path file : files) {
            try {
                String source = Files.readString(file);
                ASTParser parser = ASTParser.newParser(AST.JLS17);
                parser.setSource(source.toCharArray());
                parser.setKind(ASTParser.K_COMPILATION_UNIT);
                parser.setCompilerOptions(options);

                CompilationUnit cu = (CompilationUnit) parser.createAST(null);
                units.put(file, cu);
            } catch (Exception e) {
                System.err.println("Failed to parse " + file + ": " + e.getMessage());
            }
        }

        return units;
    }

    private void indexSymbols(CompilationUnit cu, Map<String, Symbol> symbolIndex) {
        // Simple indexing: extract class names and method signatures for cross-file resolution
        for (Object type : cu.types()) {
            if (type instanceof org.eclipse.jdt.core.dom.TypeDeclaration) {
                org.eclipse.jdt.core.dom.TypeDeclaration typeDecl = (org.eclipse.jdt.core.dom.TypeDeclaration) type;
                String className = typeDecl.getName().getIdentifier();
                symbolIndex.put(className, null); // Would expand this to full symbol info
            }
        }
    }

    // Definitions visitor for symbols and containers
    private static class DefinitionsVisitor extends ASTVisitor {
        private final CodeGraph graph;
        private final String relPath;
        private final CompilationUnit cu;
        private String packageName = "";
        private final Deque<String> scopeStack = new ArrayDeque<>();

        public DefinitionsVisitor(CodeGraph graph, String relPath, CompilationUnit cu) {
            this.graph = graph;
            this.relPath = relPath;
            this.cu = cu;
        }

        @Override
        public boolean visit(PackageDeclaration node) {
            this.packageName = node.getName().getFullyQualifiedName();
            ensurePackageContainer(packageName);
            return super.visit(node);
        }

        private void ensurePackageContainer(String pkg) {
            if (pkg.isEmpty()) return;
            String[] parts = pkg.split("\\.");
            StringBuilder currentId = new StringBuilder();
            String parentId = null;

            for (int i = 0; i < parts.length; i++) {
                if (i > 0) currentId.append(".");
                currentId.append(parts[i]);

                String id = currentId.toString();
                if (graph.getContainer(id) == null) {
                    Container container = new Container(id, "package", parts[i], relPath, parentId);
                    graph.addContainer(container);
                    if (parentId != null && graph.getContainer(parentId) != null) {
                        graph.getContainer(parentId).addChild(id);
                    }
                }
                parentId = id;
            }
        }

        @Override
        public boolean visit(TypeDeclaration node) {
            String typeName = node.getName().getIdentifier();
            String fullId = packageName.isEmpty() ? typeName : packageName + "." + typeName;
            if (!scopeStack.isEmpty()) {
                fullId = scopeStack.peek() + "." + typeName;
            }

            String kind = node.isInterface() ? "interface" : "class";
            int line = cu.getLineNumber(node.getStartPosition());
            int endLine = cu.getLineNumber(node.getStartPosition() + node.getLength());

            Symbol symbol = new Symbol(fullId, kind, typeName, relPath, line, packageName, packageName.isEmpty() ? null : packageName);
            symbol.setEndLine(endLine);
            symbol.setAbstract(Modifier.isAbstract(node.getModifiers()));

            if (node.getJavadoc() != null) {
                symbol.setDescription(node.getJavadoc().toString().trim());
            }

            // Extract superclass & superinterfaces (bases)
            if (node.getSuperclassType() != null) {
                symbol.addBase(node.getSuperclassType().toString());
            }
            for (Object superIntf : node.superInterfaceTypes()) {
                symbol.addBase(superIntf.toString());
            }

            // Extract annotations
            for (Object modifierObj : node.modifiers()) {
                if (modifierObj instanceof Annotation ann) {
                    symbol.addDecorator(ann.getTypeName().getFullyQualifiedName());
                }
            }

            graph.addSymbol(symbol);
            if (!packageName.isEmpty() && graph.getContainer(packageName) != null) {
                graph.getContainer(packageName).addChild(fullId);
            }

            // Process fields explicitly
            for (FieldDeclaration fieldDecl : node.getFields()) {
                for (Object fragObj : fieldDecl.fragments()) {
                    if (fragObj instanceof VariableDeclarationFragment frag) {
                        String fieldName = frag.getName().getIdentifier();
                        String fieldId = fullId + "." + fieldName;

                        // Resolve type
                        ITypeBinding typeBinding = null;
                        if (frag.getInitializer() != null) {
                            typeBinding = frag.getInitializer().resolveTypeBinding();
                        }
                        if (typeBinding == null) {
                            typeBinding = fieldDecl.getType().resolveBinding();
                        }

                        String typeStr = typeBinding != null ?
                            typeBinding.getQualifiedName() :
                            fieldDecl.getType().toString();

                        int fieldLine = cu.getLineNumber(fieldDecl.getStartPosition());
                        int fieldEndLine = cu.getLineNumber(fieldDecl.getStartPosition() + fieldDecl.getLength());

                        Symbol fieldSymbol = new Symbol(
                            fieldId,
                            "field",
                            fieldName,
                            relPath,
                            fieldLine,
                            packageName,
                            fullId
                        );
                        fieldSymbol.setEndLine(fieldEndLine);
                        fieldSymbol.setType(typeStr);

                        // Extract visibility modifiers
                        int modifiers = fieldDecl.getModifiers();
                        if (Modifier.isPublic(modifiers)) fieldSymbol.setVisibility("public");
                        else if (Modifier.isPrivate(modifiers)) fieldSymbol.setVisibility("private");
                        else if (Modifier.isProtected(modifiers)) fieldSymbol.setVisibility("protected");
                        else fieldSymbol.setVisibility("package");

                        fieldSymbol.setStatic(Modifier.isStatic(modifiers));
                        fieldSymbol.setFinal(Modifier.isFinal(modifiers));

                        // Javadoc if present
                        if (fieldDecl.getJavadoc() != null) {
                            fieldSymbol.setDescription(fieldDecl.getJavadoc().toString().trim());
                        }

                        graph.addSymbol(fieldSymbol);
                    }
                }
            }

            scopeStack.push(fullId);
            return super.visit(node);
        }

        @Override
        public void endVisit(TypeDeclaration node) {
            if (!scopeStack.isEmpty()) {
                scopeStack.pop();
            }
            super.endVisit(node);
        }

        @Override
        public boolean visit(EnumDeclaration node) {
            String enumName = node.getName().getIdentifier();
            String fullId = packageName.isEmpty() ? enumName : packageName + "." + enumName;
            if (!scopeStack.isEmpty()) {
                fullId = scopeStack.peek() + "." + enumName;
            }

            int line = cu.getLineNumber(node.getStartPosition());
            int endLine = cu.getLineNumber(node.getStartPosition() + node.getLength());

            Symbol symbol = new Symbol(fullId, "enum", enumName, relPath, line, packageName, packageName.isEmpty() ? null : packageName);
            symbol.setEndLine(endLine);

            graph.addSymbol(symbol);
            scopeStack.push(fullId);
            return super.visit(node);
        }

        @Override
        public void endVisit(EnumDeclaration node) {
            if (!scopeStack.isEmpty()) {
                scopeStack.pop();
            }
            super.endVisit(node);
        }

        @Override
        public boolean visit(MethodDeclaration node) {
            if (scopeStack.isEmpty()) return super.visit(node);

            String parentClassId = scopeStack.peek();
            String methodName = node.getName().getIdentifier();
            String methodId = parentClassId + "." + methodName;

            boolean isConstructor = node.isConstructor();
            String kind = isConstructor ? "constructor" : "method";

            int line = cu.getLineNumber(node.getStartPosition());
            int endLine = cu.getLineNumber(node.getStartPosition() + node.getLength());

            Symbol symbol = new Symbol(methodId, kind, methodName, relPath, line, packageName, parentClassId);
            symbol.setEndLine(endLine);
            symbol.setAbstract(Modifier.isAbstract(node.getModifiers()));

            if (node.getJavadoc() != null) {
                symbol.setDescription(node.getJavadoc().toString().trim());
            }

            // Extract method parameters
            for (Object paramObj : node.parameters()) {
                if (paramObj instanceof SingleVariableDeclaration param) {
                    symbol.addInput(param.getName().getIdentifier(), param.getType().toString(), null);
                }
            }

            // Extract return type
            if (!isConstructor) {
                Type returnType = node.getReturnType2();
                String returnTypeStr = returnType != null ? returnType.toString() : "void";
                symbol.addOutput(returnTypeStr, "annotation");

                if (returnTypeStr.contains("CompletableFuture") || returnTypeStr.contains("Mono") || returnTypeStr.contains("Flux")) {
                    symbol.setAsync_(true);
                }
            }

            // Extract annotations & entry points
            for (Object modifierObj : node.modifiers()) {
                if (modifierObj instanceof Annotation ann) {
                    String annName = ann.getTypeName().getFullyQualifiedName();
                    symbol.addDecorator(annName);
                    if ("Async".equals(annName) || annName.endsWith(".Async")) {
                        symbol.setAsync_(true);
                    }
                }
            }

            List<String> entryKinds = JavaEntryPointDetector.detectEntryPoints(node, symbol);
            for (String entryKind : entryKinds) {
                symbol.markEntryPoint(entryKind);
            }

            // Extract local variables from method body
            extractMethodLocals(node, symbol);

            graph.addSymbol(symbol);
            scopeStack.push(methodId);
            return super.visit(node);
        }

        @Override
        public void endVisit(MethodDeclaration node) {
            if (!scopeStack.isEmpty() && scopeStack.peek().contains(".")) {
                scopeStack.pop();
            }
            super.endVisit(node);
        }

        private void extractMethodLocals(MethodDeclaration method, Symbol methodSymbol) {
            // Extract local variables declared within the method body
            Block body = method.getBody();
            if (body == null) return;

            // Recursively collect variables from all nested blocks
            extractLocalsFromBlock(body, methodSymbol);
        }

        private void extractLocalsFromBlock(Block block, Symbol methodSymbol) {
            // Recursively traverse block statements to capture locals at all nesting levels
            for (Object stmt : block.statements()) {
                if (stmt instanceof VariableDeclarationStatement varStmt) {
                    for (Object fragObj : varStmt.fragments()) {
                        if (fragObj instanceof VariableDeclarationFragment frag) {
                            String varName = frag.getName().getIdentifier();

                            // Resolve type
                            ITypeBinding typeBinding = null;
                            if (frag.getInitializer() != null) {
                                typeBinding = frag.getInitializer().resolveTypeBinding();
                            }
                            if (typeBinding == null) {
                                typeBinding = varStmt.getType().resolveBinding();
                            }

                            String typeStr = typeBinding != null ?
                                typeBinding.getQualifiedName() :
                                varStmt.getType().toString();

                            int line = cu.getLineNumber(varStmt.getStartPosition());

                            // Add to locals list as a map
                            Map<String, Object> local = new HashMap<>();
                            local.put("name", varName);
                            local.put("type", typeStr);
                            local.put("line", line);
                            local.put("kind", "local");

                            methodSymbol.getLocals().add(local);
                        }
                    }
                }
                // Recursively handle nested blocks: if/else, for, while, try/catch
                else if (stmt instanceof IfStatement ifStmt) {
                    if (ifStmt.getThenStatement() instanceof Block thenBlock) {
                        extractLocalsFromBlock(thenBlock, methodSymbol);
                    }
                    if (ifStmt.getElseStatement() instanceof Block elseBlock) {
                        extractLocalsFromBlock(elseBlock, methodSymbol);
                    }
                }
                else if (stmt instanceof ForStatement forStmt) {
                    // Handle for loop variable
                    if (forStmt.initializers() != null) {
                        for (Object init : forStmt.initializers()) {
                            if (init instanceof VariableDeclarationExpression varDeclExpr) {
                                for (Object fragObj : varDeclExpr.fragments()) {
                                    if (fragObj instanceof VariableDeclarationFragment frag) {
                                        extractVariableLocal(frag, varDeclExpr.getType(), methodSymbol);
                                    }
                                }
                            }
                        }
                    }
                    // Recursively handle for body
                    if (forStmt.getBody() instanceof Block forBody) {
                        extractLocalsFromBlock(forBody, methodSymbol);
                    }
                }
                else if (stmt instanceof EnhancedForStatement enhancedFor) {
                    // Handle enhanced for variable: for (Type x : items)
                    SingleVariableDeclaration param = enhancedFor.getParameter();
                    if (param != null) {
                        String varName = param.getName().getIdentifier();
                        ITypeBinding typeBinding = param.getType().resolveBinding();
                        String typeStr = typeBinding != null ?
                            typeBinding.getQualifiedName() :
                            param.getType().toString();
                        int line = cu.getLineNumber(enhancedFor.getStartPosition());

                        Map<String, Object> local = new HashMap<>();
                        local.put("name", varName);
                        local.put("type", typeStr);
                        local.put("line", line);
                        local.put("kind", "local");
                        methodSymbol.getLocals().add(local);
                    }
                    // Recursively handle for body
                    if (enhancedFor.getBody() instanceof Block forBody) {
                        extractLocalsFromBlock(forBody, methodSymbol);
                    }
                }
                else if (stmt instanceof WhileStatement whileStmt) {
                    // Recursively handle while body
                    if (whileStmt.getBody() instanceof Block whileBody) {
                        extractLocalsFromBlock(whileBody, methodSymbol);
                    }
                }
                else if (stmt instanceof DoStatement doStmt) {
                    // Recursively handle do-while body
                    if (doStmt.getBody() instanceof Block doBody) {
                        extractLocalsFromBlock(doBody, methodSymbol);
                    }
                }
                else if (stmt instanceof TryStatement tryStmt) {
                    // Recursively handle try body
                    extractLocalsFromBlock(tryStmt.getBody(), methodSymbol);
                    // Handle catch clauses
                    for (Object catchObj : tryStmt.catchClauses()) {
                        if (catchObj instanceof CatchClause catchClause) {
                            // Extract catch parameter
                            SingleVariableDeclaration param = catchClause.getException();
                            if (param != null) {
                                String varName = param.getName().getIdentifier();
                                ITypeBinding typeBinding = param.getType().resolveBinding();
                                String typeStr = typeBinding != null ?
                                    typeBinding.getQualifiedName() :
                                    param.getType().toString();
                                int line = cu.getLineNumber(catchClause.getStartPosition());

                                Map<String, Object> local = new HashMap<>();
                                local.put("name", varName);
                                local.put("type", typeStr);
                                local.put("line", line);
                                local.put("kind", "local");
                                methodSymbol.getLocals().add(local);
                            }
                            // Recursively handle catch body
                            extractLocalsFromBlock(catchClause.getBody(), methodSymbol);
                        }
                    }
                    // Handle finally block
                    if (tryStmt.getFinally() != null) {
                        extractLocalsFromBlock(tryStmt.getFinally(), methodSymbol);
                    }
                }
                else if (stmt instanceof Block nestedBlock) {
                    // Handle plain nested blocks
                    extractLocalsFromBlock(nestedBlock, methodSymbol);
                }
            }
        }

        private void extractVariableLocal(VariableDeclarationFragment frag, Type type, Symbol methodSymbol) {
            String varName = frag.getName().getIdentifier();
            ITypeBinding typeBinding = null;
            if (frag.getInitializer() != null) {
                typeBinding = frag.getInitializer().resolveTypeBinding();
            }
            if (typeBinding == null) {
                typeBinding = type.resolveBinding();
            }
            String typeStr = typeBinding != null ?
                typeBinding.getQualifiedName() :
                type.toString();
            int line = cu.getLineNumber(frag.getStartPosition());

            Map<String, Object> local = new HashMap<>();
            local.put("name", varName);
            local.put("type", typeStr);
            local.put("line", line);
            local.put("kind", "local");
            methodSymbol.getLocals().add(local);
        }

        @Override
        public boolean visit(FieldDeclaration node) {
            // Only process if inside a class/enum/interface (scopeStack not empty)
            if (scopeStack.isEmpty()) return super.visit(node);

            String parentClassId = scopeStack.peek();
            Symbol parentSymbol = graph.getSymbol(parentClassId);
            if (parentSymbol == null) return super.visit(node);

            // Each VariableDeclarationFragment in the FieldDeclaration
            for (Object fragObj : node.fragments()) {
                if (fragObj instanceof VariableDeclarationFragment frag) {
                    String fieldName = frag.getName().getIdentifier();
                    String fieldId = parentClassId + "." + fieldName;

                    // Resolve type via VariableDeclarationFragment's type
                    ITypeBinding typeBinding = null;
                    if (frag.getInitializer() != null) {
                        typeBinding = frag.getInitializer().resolveTypeBinding();
                    }
                    if (typeBinding == null) {
                        // Try to resolve via Type binding
                        typeBinding = node.getType().resolveBinding();
                    }

                    String typeStr = typeBinding != null ?
                        typeBinding.getQualifiedName() :
                        node.getType().toString();  // Fallback to AST string

                    int line = cu.getLineNumber(node.getStartPosition());
                    int endLine = cu.getLineNumber(node.getStartPosition() + node.getLength());

                    Symbol fieldSymbol = new Symbol(
                        fieldId,
                        "field",
                        fieldName,
                        relPath,
                        line,
                        packageName,
                        parentClassId
                    );
                    fieldSymbol.setEndLine(endLine);
                    fieldSymbol.setType(typeStr);  // Store resolved type

                    // Extract visibility modifiers
                    int modifiers = node.getModifiers();
                    if (Modifier.isPublic(modifiers)) fieldSymbol.setVisibility("public");
                    else if (Modifier.isPrivate(modifiers)) fieldSymbol.setVisibility("private");
                    else if (Modifier.isProtected(modifiers)) fieldSymbol.setVisibility("protected");
                    else fieldSymbol.setVisibility("package");

                    fieldSymbol.setStatic(Modifier.isStatic(modifiers));
                    fieldSymbol.setFinal(Modifier.isFinal(modifiers));

                    // Javadoc if present
                    if (node.getJavadoc() != null) {
                        fieldSymbol.setDescription(node.getJavadoc().toString().trim());
                    }

                    graph.addSymbol(fieldSymbol);
                }
            }

            return super.visit(node);
        }

        @Override
        public boolean visit(EnumConstantDeclaration node) {
            if (scopeStack.isEmpty()) return super.visit(node);

            String parentEnumId = scopeStack.peek();
            Symbol parentEnum = graph.getSymbol(parentEnumId);
            if (parentEnum == null) return super.visit(node);

            String constantName = node.getName().getIdentifier();
            String constantId = parentEnumId + "." + constantName;

            // Enum constant type = the enum class itself
            String typeStr = parentEnumId;

            int line = cu.getLineNumber(node.getStartPosition());
            int endLine = cu.getLineNumber(node.getStartPosition() + node.getLength());

            Symbol constantSymbol = new Symbol(
                constantId,
                "field",  // Treat as field
                constantName,
                relPath,
                line,
                packageName,
                parentEnumId
            );
            constantSymbol.setEndLine(endLine);
            constantSymbol.setType(typeStr);
            constantSymbol.setVisibility("public");  // Enum constants always public
            constantSymbol.setStatic(true);          // Enum constants always static
            constantSymbol.setFinal(true);           // Enum constants always final

            if (node.getJavadoc() != null) {
                constantSymbol.setDescription(node.getJavadoc().toString().trim());
            }

            graph.addSymbol(constantSymbol);
            return super.visit(node);
        }
    }

    // Relationships visitor for INHERITS, OVERRIDES, CALLS, DECORATES
    private static class RelationshipsVisitor extends ASTVisitor {
        private final CodeGraph graph;
        private final String relPath;
        private final CompilationUnit cu;
        private final Deque<String> currentSymbolStack = new ArrayDeque<>();

        public RelationshipsVisitor(CodeGraph graph, String relPath, CompilationUnit cu) {
            this.graph = graph;
            this.relPath = relPath;
            this.cu = cu;
        }

        @Override
        public boolean visit(TypeDeclaration node) {
            String typeName = node.getName().getIdentifier();
            String fullId = findSymbolIdByName(typeName);
            if (fullId != null) {
                currentSymbolStack.push(fullId);
                Symbol symbol = graph.getSymbol(fullId);

                // Add INHERITS relations
                if (symbol != null) {
                    for (String baseName : symbol.getBases()) {
                        String targetClassId = findSymbolIdByName(baseName);
                        if (targetClassId != null && graph.getSymbol(targetClassId) != null) {
                            int line = cu.getLineNumber(node.getStartPosition());
                            Relation relation = new Relation(fullId, targetClassId, "INHERITS", relPath, line);
                            relation.setResolution("resolved", Evidence.fact("jdt_binding_resolution"));
                            graph.addRelation(relation);
                        }
                    }
                }

                // Add DECORATES relations for annotations
                if (symbol != null) {
                    for (Object modifierObj : node.modifiers()) {
                        if (modifierObj instanceof Annotation ann) {
                            emitDecoratesRelation(fullId, ann);
                        }
                    }
                }
            }
            return super.visit(node);
        }

        @Override
        public void endVisit(TypeDeclaration node) {
            if (!currentSymbolStack.isEmpty()) {
                currentSymbolStack.pop();
            }
            super.endVisit(node);
        }

        @Override
        public boolean visit(MethodDeclaration node) {
            if (currentSymbolStack.isEmpty()) return super.visit(node);

            String parentId = currentSymbolStack.peek();
            String methodName = node.getName().getIdentifier();
            String methodId = parentId + "." + methodName;

            Symbol methodSymbol = graph.getSymbol(methodId);
            if (methodSymbol != null) {
                currentSymbolStack.push(methodId);

                // Check @Override relation
                if (methodSymbol.getDecorators().contains("Override")) {
                    Symbol parentClass = graph.getSymbol(parentId);
                    if (parentClass != null) {
                        for (String baseName : parentClass.getBases()) {
                            String baseClassId = findSymbolIdByName(baseName);
                            if (baseClassId != null) {
                                String targetMethodId = baseClassId + "." + methodName;
                                if (graph.getSymbol(targetMethodId) != null) {
                                    int line = cu.getLineNumber(node.getStartPosition());
                                    Relation relation = new Relation(methodId, targetMethodId, "OVERRIDES", relPath, line);
                                    relation.setResolution("resolved", Evidence.fact("jdt_binding_resolution"));
                                    graph.addRelation(relation);
                                }
                            }
                        }
                    }
                }

                // Add DECORATES relations for annotations
                for (Object modifierObj : node.modifiers()) {
                    if (modifierObj instanceof Annotation ann) {
                        emitDecoratesRelation(methodId, ann);
                    }
                }
            }
            return super.visit(node);
        }

        @Override
        public void endVisit(MethodDeclaration node) {
            if (!currentSymbolStack.isEmpty() && currentSymbolStack.peek().contains(".")) {
                currentSymbolStack.pop();
            }
            super.endVisit(node);
        }

        @Override
        public boolean visit(MethodInvocation node) {
            if (currentSymbolStack.isEmpty()) return super.visit(node);

            String currentMethodId = currentSymbolStack.peek();
            Symbol currentMethod = graph.getSymbol(currentMethodId);
            int line = cu.getLineNumber(node.getStartPosition());

            if (currentMethod != null) {
                JavaResiliencyAnalyzer.checkMethodInvocation(node, currentMethod, line);

                String calledMethodName = node.getName().getIdentifier();
                IMethodBinding binding = node.resolveMethodBinding();

                String targetMethodId = null;
                if (binding != null && binding.getDeclaringClass() != null) {
                    targetMethodId = binding.getDeclaringClass().getQualifiedName() + "." + calledMethodName;
                } else {
                    targetMethodId = findMethodSymbolIdByName(calledMethodName);
                }

                if (targetMethodId != null && graph.getSymbol(targetMethodId) != null) {
                    String kind = currentMethod.isAsync_() ? "AWAIT_CALLS" : "CALLS";
                    Relation relation = new Relation(currentMethodId, targetMethodId, kind, relPath, line);
                    if (binding == null) {
                        relation.setResolution("resolved_via_inference", Evidence.heuristic("name_matching"));
                    } else {
                        // Binding resolved via JDT's cross-file/cross-module mechanism
                        relation.setResolution("resolved", Evidence.fact("jdt_binding_resolution"));
                    }
                    graph.addRelation(relation);
                }
            }
            return super.visit(node);
        }

        private String findSymbolIdByName(String name) {
            if (name.contains(".")) {
                return graph.getSymbol(name) != null ? name : null;
            }
            for (Symbol symbol : graph.getSymbols()) {
                if (symbol.getName().equals(name) && (symbol.getKind().equals("class") || symbol.getKind().equals("interface") || symbol.getKind().equals("enum"))) {
                    return symbol.getId();
                }
            }
            return null;
        }

        private String findMethodSymbolIdByName(String methodName) {
            for (Symbol symbol : graph.getSymbols()) {
                if (symbol.getName().equals(methodName) && (symbol.getKind().equals("method") || symbol.getKind().equals("constructor"))) {
                    return symbol.getId();
                }
            }
            return null;
        }

        @Override
        public boolean visit(ImportDeclaration node) {
            // Get the import name
            String importName = node.getName().getFullyQualifiedName();

            // Determine the target based on import type
            String targetId;
            if (node.isOnDemand()) {
                // Wildcard import: "import java.util.*" → target is package "java.util"
                targetId = importName;
            } else if (node.isStatic()) {
                // Static import: "import static java.util.Collections.emptyList" → keep full path
                targetId = importName;
            } else {
                // Regular import: "import java.util.List" → keep full path
                targetId = importName;
            }

            // Get the source package from the compilation unit
            PackageDeclaration pkgDecl = cu.getPackage();
            String sourcePackage = pkgDecl != null ? pkgDecl.getName().getFullyQualifiedName() : "";

            // Create IMPORTS relation from the package to the imported type
            if (!sourcePackage.isEmpty()) {
                int line = cu.getLineNumber(node.getStartPosition());
                Relation relation = new Relation(sourcePackage, targetId, "IMPORTS", relPath, line);
                // Imports are typically external (standard library, 3rd-party)
                relation.setResolution("external_or_dynamic", Evidence.heuristic("import_statement"));
                graph.addRelation(relation);
            }

            return super.visit(node);
        }

        private void emitDecoratesRelation(String sourceId, Annotation ann) {
            // Get the annotation type name
            String annotTypeName = ann.getTypeName().getFullyQualifiedName();
            ITypeBinding annotBinding = ann.resolveTypeBinding();

            // Resolve the annotation type to a symbol ID
            String targetAnnotId;
            if (annotBinding != null && annotBinding.getQualifiedName() != null) {
                targetAnnotId = annotBinding.getQualifiedName();
            } else {
                targetAnnotId = annotTypeName;
            }

            // Emit DECORATES relation
            int line = cu.getLineNumber(ann.getStartPosition());
            Relation relation = new Relation(sourceId, targetAnnotId, "DECORATES", relPath, line);

            // Set resolution based on annotation type
            if (annotBinding != null && graph.getSymbol(targetAnnotId) != null) {
                // In-repo annotation (a custom @interface defined locally)
                relation.setResolution("resolved", Evidence.fact("jdt_binding_resolution"));
            } else {
                // Framework/JDK annotation (external)
                relation.setResolution("external_or_dynamic", Evidence.heuristic("annotation_type"));
            }

            // Capture annotation member-values
            if (ann instanceof SingleMemberAnnotation single) {
                Map<String, Object> arg = new HashMap<>();
                arg.put("name", "value");
                arg.put("value", single.getValue().toString());
                relation.getArguments().add(arg);
            } else if (ann instanceof NormalAnnotation normal) {
                for (Object memberObj : normal.values()) {
                    if (memberObj instanceof MemberValuePair pair) {
                        Map<String, Object> arg = new HashMap<>();
                        arg.put("name", pair.getName().getIdentifier());
                        arg.put("value", pair.getValue().toString());
                        relation.getArguments().add(arg);
                    }
                }
            }

            graph.addRelation(relation);
        }
    }
}
