package com.lineagelens.parser;

import com.lineagelens.model.CodeGraph;
import com.lineagelens.model.Container;
import com.lineagelens.model.Evidence;
import com.lineagelens.model.Relation;
import com.lineagelens.model.Symbol;
import org.eclipse.jdt.core.JavaCore;
import org.eclipse.jdt.core.dom.*;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
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

    private Map<Path, CompilationUnit> parseCompilationUnits(List<Path> files) {
        Map<Path, CompilationUnit> units = new HashMap<>();

        for (Path file : files) {
            try {
                String source = Files.readString(file);
                ASTParser parser = ASTParser.newParser(AST.JLS17);
                parser.setSource(source.toCharArray());
                parser.setKind(ASTParser.K_COMPILATION_UNIT);
                parser.setResolveBindings(true);
                parser.setBindingsRecovery(true);

                Map<String, String> options = JavaCore.getOptions();
                options.put(JavaCore.COMPILER_SOURCE, JavaCore.VERSION_17);
                parser.setCompilerOptions(options);

                CompilationUnit cu = (CompilationUnit) parser.createAST(null);
                units.put(file, cu);
            } catch (Exception e) {
                System.err.println("Failed to parse Java file " + file + ": " + e.getMessage());
            }
        }
        return units;
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
                            graph.addRelation(new Relation(fullId, targetClassId, "INHERITS", relPath, line));
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
                                    graph.addRelation(new Relation(methodId, targetMethodId, "OVERRIDES", relPath, line));
                                }
                            }
                        }
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
    }
}
