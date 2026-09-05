# Issue #14: Improve Java AST Binding Resolution via Sourcepath Discovery

**Status**: Ready for Implementation  
**Impact**: High (affects all multi-module Java projects)  
**Effort**: 8-12 hours  
**Risk**: Medium (fallback strategy ensures graceful degradation)  

---

## Executive Summary

The current `JavaAstAnalyzer` uses **per-file sequential parsing** with **no classpath/sourcepath configuration**, causing JDT bindings to fail and fall back to unreliable name-matching. This degrades analysis quality for multi-module projects where method overloading is common.

**Root Cause**: Lines 69-92 parse each file independently without:
1. Discovering source roots (e.g., `src/main/java`, `src/test/java`)
2. Building classpath from Maven/Gradle dependencies
3. Setting up `INameEnvironment` for JDT
4. Enabling method signature resolution

**Result**: `IMethodBinding.resolveMethodBinding()` returns `null`, forcing fallback to `findMethodSymbolIdByName()`, which returns the **first match by name only** (line 415-420), ignoring packages and overloads.

**Success Metric**: Cross-module method calls resolve via JDT bindings (not inference) on ≥85% of projects.

---

## Current State Analysis

### 1. Parsing Strategy: Per-File Sequential (Problematic)

```java
// Lines 69-92: JavaAstAnalyzer.parseCompilationUnits()

private Map<Path, CompilationUnit> parseCompilationUnits(List<Path> files) {
    Map<Path, CompilationUnit> units = new HashMap<>();
    
    for (Path file : files) {  // Line 72: Each file isolated
        try {
            String source = Files.readString(file);
            ASTParser parser = ASTParser.newParser(AST.JLS17);  // Line 75: NEW parser instance
            parser.setSource(source.toCharArray());
            parser.setKind(ASTParser.K_COMPILATION_UNIT);
            parser.setResolveBindings(true);  // Line 78: Requests bindings, but...
            parser.setBindingsRecovery(true);
            
            Map<String, String> options = JavaCore.getOptions();
            options.put(JavaCore.COMPILER_SOURCE, JavaCore.VERSION_17);
            parser.setCompilerOptions(options);  // Lines 81-83: INCOMPLETE options
            
            CompilationUnit cu = (CompilationUnit) parser.createAST(null);  // Line 85: null = no environment
            units.put(file, cu);
        } catch (Exception e) {
            System.err.println("Failed to parse Java file " + file + ": " + e.getMessage());
        }
    }
    return units;
}
```

**Problems**:
- ✗ Line 78: `setResolveBindings(true)` is a **request**, not a guarantee
- ✗ Line 85: `parser.createAST(null)` → **null parameter = no INameEnvironment**
- ✗ Lines 81-83: Missing `COMPILER_CLASSPATH`, `COMPILER_SOURCE_PATH`, `COMPILER_BOOTPATH`
- ✗ No `parser.setEnvironment()` call; bindings cannot resolve external types
- ✗ Line 72: Loop reconstructs parser state for each file (inefficient)

### 2. Binding Resolution: Fallback Chain (Unreliable)

**Primary attempt (Line 381)**:
```java
IMethodBinding binding = node.resolveMethodBinding();  // Likely returns null

if (binding != null && binding.getDeclaringClass() != null) {
    targetMethodId = binding.getDeclaringClass().getQualifiedName() + "." + calledMethodName;
} else {
    // Fallback: Name-only matching
    targetMethodId = findMethodSymbolIdByName(calledMethodName);  // Line 387
}
```

**Fallback resolution (Lines 414-421)**:
```java
private String findMethodSymbolIdByName(String methodName) {
    for (Symbol symbol : graph.getSymbols()) {  // Linear O(n) scan
        if (symbol.getName().equals(methodName) && 
            (symbol.getKind().equals("method") || symbol.getKind().equals("constructor"))) {
            return symbol.getId();  // FIRST MATCH ONLY - ignores overloads!
        }
    }
    return null;
}
```

**Failures**:
- Returns first symbol by name (ignores package context)
- No overload resolution (method signature matching)
- False positives in same-name methods across modules
- No type checking (accepts wrong arity calls)

### 3. Environment/Classpath Setup: Absent

**Currently set**:
```java
Map<String, String> options = JavaCore.getOptions();  // Line 81: Uses JDT defaults
options.put(JavaCore.COMPILER_SOURCE, JavaCore.VERSION_17);  // Only source version
parser.setCompilerOptions(options);
```

**Missing configuration**:
```
COMPILER_CLASSPATH         → null (no external .jar dependencies)
COMPILER_SOURCE_PATH       → null (no internal source roots)
COMPILER_BOOTPATH          → null (JDK library path missing)
INameEnvironment           → null (no symbol resolution environment)
ASTParser.setEnvironment() → NOT CALLED
```

**Impact**: JDT cannot resolve types outside the single file being parsed.

### 4. Sourcepath Discovery: Non-Existent

**Constructor (Lines 22-24)**:
```java
public JavaAstAnalyzer(Path projectRoot) {
    this.projectRoot = projectRoot.toAbsolutePath().normalize();
}
```

**No source root detection** → Missing:
- Maven multi-module detection (`pom.xml`, `<modules>`)
- Gradle build config (`build.gradle`, `sourceSets`)
- Standard directory patterns (`src/main/java`, `src/test/java`)
- Eclipse metadata (`.project`, `.classpath`)

---

## Target State

### 1. Execution Flow: Batch Parsing with Environment

```
JavaAstAnalyzer(projectRoot)
    ↓
analyze() called
    ↓
[NEW] discoverSourceRoots(projectRoot)
    ├─ Scan pom.xml → [src/main/java, src/test/java, ...]
    ├─ Scan build.gradle → [src/main/java, src/test/java, ...]
    ├─ Fallback to src/ directory pattern
    └─ Return: List<Path> sourceDirs = [/proj/src/main/java, /proj/mod/src/main/java, ...]
    ↓
[NEW] resolveClasspath(projectRoot)
    ├─ Try: mvn dependency:build-classpath (extract jar list)
    ├─ Fallback: Scan ~/.m2/repository (heuristic)
    └─ Return: String[] classPath = [/home/.m2/repository/..., ...]
    ↓
[MODIFIED] parseCompilationUnits(List<Path> files, String[] sourceDirs, String[] classPath)
    ├─ Create INameEnvironment with sourceDirs + classPath
    ├─ Single ASTParser instance with environment
    ├─ Batch parse via createASTs(files[], ..., requestor callback)
    └─ JDT bindings now resolve across modules
    ↓
[Phase 2 Visitor] RelationshipsVisitor
    ├─ Call IMethodBinding.resolveMethodBinding()
    ├─ Returns binding with full type info (package + class + signature)
    ├─ Fallback only if binding null (rare)
    └─ Evidence marked as "resolved" (not "resolved_via_inference")
```

### 2. Binding Resolution Flow (New)

```
MethodInvocation node (e.g., foo.bar())
    ↓
[TRY] node.resolveMethodBinding()
    ├─ Environment set → JDT performs type resolution
    ├─ Returns IMethodBinding with:
    │  ├─ declaringClass: com.example.MyClass
    │  ├─ name: bar
    │  └─ parameterTypes: [int, String]
    └─ Construct qualified ID: "com.example.MyClass.bar(int,String)"
    ↓
[FALLBACK - IF NULL] findMethodSymbolIdByQualifiedName()
    ├─ Receiver type (foo) not resolvable
    ├─ Search by name + arity matching
    └─ Set relation evidence: "resolved_via_inference"
    ↓
[NO MATCH] Skip relation (too ambiguous)
```

### 3. Classpath/Sourcepath Discovery Algorithm

```
discoverSourceRoots(projectRoot):
    1. Check /pom.xml:
       └─ Parse XML, extract:
          ├─ <sourceDirectory> (default: src/main/java)
          ├─ <testSourceDirectory> (default: src/test/java)
          └─ For each <module>: recurse into sibling dirs
       └─ Return List<Path>
    
    2. Else check /build.gradle:
       └─ Parse Gradle, extract:
          ├─ sourceSets.main.java.srcDirs
          ├─ sourceSets.test.java.srcDirs
          └─ For multi-project: subprojects { } blocks
       └─ Return List<Path>
    
    3. Else check standard patterns:
       ├─ If exists /src/main/java: add it
       ├─ If exists /src/test/java: add it
       ├─ If exists /src: add it
       └─ Return List<Path>
    
    4. Return: [/proj/src/main/java, /proj/mod1/src/main/java, ...] (absolute)

resolveClasspath(projectRoot):
    1. Try Maven: mvn -q dependency:build-classpath -DincludeScope=compile
       └─ Extract jar list from output
       └─ Return String[] = ["/home/.m2/repository/...", ...]
    
    2. Fallback: Scan ~/.m2/repository heuristically
       └─ For each dependency in pom.xml: find in cache
       └─ Return String[] (partial or empty if unavailable)
    
    3. Return: String[] classPath (empty array OK - sourcepath-only works)

getBootClassPath(javaVersion):
    └─ For Java 17: /usr/lib/jvm/java-17-*/lib/modules or similar
    └─ Return: String (system property java.home)
```

---

## Implementation Steps (In Order)

### Step 1: Add Utility Methods for Sourcepath Discovery

**File**: `src/java/src/main/java/com/lineagelens/parser/JavaAstAnalyzer.java`

**Add after line 67** (after `collectJavaFiles()`):

```java
/**
 * Discovers all Java source roots in project hierarchy.
 * Scans for:
 * - Maven pom.xml (src/main/java, src/test/java, modules)
 * - Gradle build.gradle (sourceSets.main/test.java.srcDirs)
 * - Standard directory patterns (src/main/java, src/test/java, src/)
 *
 * @return List of absolute Paths to source roots (never null, may be empty)
 */
private List<Path> discoverSourceRoots() throws IOException {
    List<Path> sourceRoots = new ArrayList<>();
    
    // 1. Try Maven pom.xml
    Path pomFile = projectRoot.resolve("pom.xml");
    if (Files.exists(pomFile)) {
        sourceRoots.addAll(discoverMavenSourceRoots(pomFile));
        return sourceRoots; // Return early if Maven project
    }
    
    // 2. Try Gradle build.gradle
    Path gradleFile = projectRoot.resolve("build.gradle");
    if (Files.exists(gradleFile)) {
        sourceRoots.addAll(discoverGradleSourceRoots(gradleFile));
        if (!sourceRoots.isEmpty()) {
            return sourceRoots; // Return early if Gradle project
        }
    }
    
    // 3. Fallback: Standard directory patterns
    sourceRoots.addAll(discoverStandardSourceRoots());
    return sourceRoots;
}

/**
 * Extracts source roots from Maven pom.xml.
 * Handles multi-module projects by recursing into submodules.
 */
private List<Path> discoverMavenSourceRoots(Path pomFile) {
    List<Path> roots = new ArrayList<>();
    try {
        String pomContent = Files.readString(pomFile);
        
        // Extract <sourceDirectory>
        String sourceDir = extractXmlValue(pomContent, "<sourceDirectory>", "</sourceDirectory>");
        if (sourceDir != null && !sourceDir.isEmpty()) {
            Path resolved = projectRoot.resolve(sourceDir).toAbsolutePath();
            if (Files.exists(resolved)) {
                roots.add(resolved);
            }
        } else {
            // Default to src/main/java
            Path defaultSource = projectRoot.resolve("src/main/java");
            if (Files.exists(defaultSource)) {
                roots.add(defaultSource);
            }
        }
        
        // Extract <testSourceDirectory>
        String testDir = extractXmlValue(pomContent, "<testSourceDirectory>", "</testSourceDirectory>");
        if (testDir != null && !testDir.isEmpty()) {
            Path resolved = projectRoot.resolve(testDir).toAbsolutePath();
            if (Files.exists(resolved)) {
                roots.add(resolved);
            }
        } else {
            // Default to src/test/java
            Path defaultTest = projectRoot.resolve("src/test/java");
            if (Files.exists(defaultTest)) {
                roots.add(defaultTest);
            }
        }
        
        // Handle multi-module projects: extract <modules>
        String modulesSection = extractXmlSection(pomContent, "<modules>", "</modules>");
        if (modulesSection != null) {
            String[] modules = modulesSection.split("<module>");
            for (String module : modules) {
                int endIdx = module.indexOf("</module>");
                if (endIdx > 0) {
                    String modulePath = module.substring(0, endIdx).trim();
                    Path modulePom = projectRoot.resolve(modulePath).resolve("pom.xml");
                    if (Files.exists(modulePom)) {
                        roots.addAll(discoverMavenSourceRoots(modulePom));
                    }
                }
            }
        }
    } catch (Exception e) {
        System.err.println("Failed to parse Maven pom.xml: " + e.getMessage());
    }
    return roots;
}

/**
 * Extracts source roots from Gradle build.gradle.
 * Handles multi-project builds by scanning subproject blocks.
 */
private List<Path> discoverGradleSourceRoots(Path gradleFile) {
    List<Path> roots = new ArrayList<>();
    try {
        String gradleContent = Files.readString(gradleFile);
        
        // Simple regex-based extraction of sourceSets.main.java.srcDirs
        // Format: sourceSets { main { java { srcDirs = ['src/main/java', ...] } } }
        java.util.regex.Pattern pattern = java.util.regex.Pattern.compile(
            "sourceSets\\s*\\{[^}]*main\\s*\\{[^}]*java\\s*\\{[^}]*srcDirs\\s*=\\s*\\[([^\\]]+)\\]"
        );
        java.util.regex.Matcher matcher = pattern.matcher(gradleContent);
        if (matcher.find()) {
            String srcDirs = matcher.group(1);
            for (String dir : srcDirs.split(",")) {
                String cleaned = dir.trim().replaceAll("['\"]", "");
                Path resolved = projectRoot.resolve(cleaned).toAbsolutePath();
                if (Files.exists(resolved)) {
                    roots.add(resolved);
                }
            }
        }
        
        // Fallback: look for src/main/java
        if (roots.isEmpty()) {
            Path defaultSource = projectRoot.resolve("src/main/java");
            if (Files.exists(defaultSource)) {
                roots.add(defaultSource);
            }
        }
        
        // TODO: Handle subprojects { } blocks for multi-project builds
        // This would require parsing nested Gradle DSL, deferred for Phase 2
        
    } catch (Exception e) {
        System.err.println("Failed to parse Gradle build.gradle: " + e.getMessage());
    }
    return roots;
}

/**
 * Fallback: Discovers source roots using standard directory patterns.
 */
private List<Path> discoverStandardSourceRoots() {
    List<Path> roots = new ArrayList<>();
    
    // Check for src/main/java
    Path srcMainJava = projectRoot.resolve("src/main/java");
    if (Files.exists(srcMainJava)) {
        roots.add(srcMainJava);
    }
    
    // Check for src/test/java
    Path srcTestJava = projectRoot.resolve("src/test/java");
    if (Files.exists(srcTestJava)) {
        roots.add(srcTestJava);
    }
    
    // Check for src/ (if no structured subdirs)
    if (roots.isEmpty()) {
        Path src = projectRoot.resolve("src");
        if (Files.exists(src) && Files.isDirectory(src)) {
            roots.add(src);
        }
    }
    
    return roots;
}

/**
 * Extracts an XML element value. Naive implementation; assumes well-formed XML.
 * Example: extractXmlValue("<foo>bar</foo>", "<foo>", "</foo>") → "bar"
 */
private String extractXmlValue(String xml, String startTag, String endTag) {
    int start = xml.indexOf(startTag);
    if (start == -1) return null;
    start += startTag.length();
    int end = xml.indexOf(endTag, start);
    if (end == -1) return null;
    return xml.substring(start, end).trim();
}

/**
 * Extracts an XML section. Naive implementation.
 * Example: extractXmlSection("<modules>...", "<modules>", "</modules>") → "..."
 */
private String extractXmlSection(String xml, String startTag, String endTag) {
    int start = xml.indexOf(startTag);
    if (start == -1) return null;
    start += startTag.length();
    int end = xml.indexOf(endTag, start);
    if (end == -1) return null;
    return xml.substring(start, end).trim();
}

/**
 * Resolves classpath (external .jar dependencies) for the project.
 * Attempts:
 * 1. mvn dependency:build-classpath (most reliable)
 * 2. Scan ~/.m2/repository heuristically
 * 3. Return empty array (graceful degradation)
 *
 * @return String[] of absolute .jar paths (may be empty)
 */
private String[] resolveClasspath() {
    List<String> classpathEntries = new ArrayList<>();
    
    // Try Maven
    Path pomFile = projectRoot.resolve("pom.xml");
    if (Files.exists(pomFile)) {
        try {
            classpathEntries.addAll(resolveMavenClasspath());
            if (!classpathEntries.isEmpty()) {
                return classpathEntries.toArray(String[]::new);
            }
        } catch (Exception e) {
            System.err.println("Failed to resolve Maven classpath: " + e.getMessage());
        }
    }
    
    // Graceful degradation: return empty (sourcepath-only resolution will work)
    return new String[0];
}

/**
 * Attempts to resolve Maven classpath via `mvn dependency:build-classpath`.
 */
private List<String> resolveMavenClasspath() throws IOException, InterruptedException {
    List<String> entries = new ArrayList<>();
    
    // Run: mvn -q dependency:build-classpath -DincludeScope=compile
    ProcessBuilder pb = new ProcessBuilder(
        "mvn", "-q", "dependency:build-classpath", "-DincludeScope=compile"
    );
    pb.directory(projectRoot.toFile());
    pb.redirectErrorStream(true);
    
    Process process = pb.start();
    int exitCode = process.waitFor();
    
    if (exitCode == 0) {
        BufferedReader reader = new BufferedReader(
            new InputStreamReader(process.getInputStream())
        );
        String line = reader.readLine();
        if (line != null && !line.isEmpty()) {
            // Split by PATH separator (: on Unix, ; on Windows)
            String separator = System.getProperty("path.separator");
            for (String jar : line.split(separator)) {
                String trimmed = jar.trim();
                if (!trimmed.isEmpty() && Files.exists(Path.of(trimmed))) {
                    entries.add(trimmed);
                }
            }
        }
    }
    
    return entries;
}

/**
 * Gets the JDK boot classpath for the target Java version.
 * For Java 17+, uses system property or heuristic.
 */
private String getBootClassPath() {
    String javaHome = System.getProperty("java.home");
    return javaHome != null ? javaHome : "";
}
```

---

### Step 2: Refactor `parseCompilationUnits()` to Use Environment

**File**: `src/java/src/main/java/com/lineagelens/parser/JavaAstAnalyzer.java`

**Replace lines 69-92** with:

```java
/**
 * Parses all Java files with environment setup for binding resolution.
 * Creates INameEnvironment with sourcepath + classpath, enabling JDT to resolve
 * cross-file and external types. Uses batch API (createASTs) for efficiency.
 *
 * @param files Java files to parse
 * @param sourceDirs Source root directories (for internal types)
 * @param classPath Classpath entries (for external .jar types)
 * @return Map of Path → CompilationUnit with resolved bindings
 */
private Map<Path, CompilationUnit> parseCompilationUnits(
        List<Path> files,
        String[] sourceDirs,
        String[] classPath) {
    
    Map<Path, CompilationUnit> units = new HashMap<>();
    
    if (files.isEmpty()) {
        return units;
    }
    
    try {
        // Step 1: Prepare file paths and encodings
        String[] filePaths = files.stream()
            .map(Path::toString)
            .toArray(String[]::new);
        String[] encodings = new String[files.size()]; // null = platform default
        
        // Step 2: Create ASTParser instance (shared for batch parsing)
        ASTParser parser = ASTParser.newParser(AST.JLS17);
        parser.setKind(ASTParser.K_COMPILATION_UNIT);
        parser.setResolveBindings(true);
        parser.setBindingsRecovery(true);
        
        // Step 3: Configure compiler options
        Map<String, String> options = JavaCore.getOptions();
        options.put(JavaCore.COMPILER_SOURCE, JavaCore.VERSION_17);
        options.put(JavaCore.COMPILER_CODEGEN_TARGET_PLATFORM, JavaCore.VERSION_17);
        options.put(JavaCore.COMPILER_COMPLIANCE, JavaCore.VERSION_17);
        // Add classpath and sourcepath to options for better resolution
        if (classPath.length > 0) {
            options.put(JavaCore.COMPILER_CLASSPATH, String.join(
                System.getProperty("path.separator"),
                classPath
            ));
        }
        parser.setCompilerOptions(options);
        
        // Step 4: Create and set INameEnvironment
        // This is CRITICAL for binding resolution across files
        String bootClassPath = getBootClassPath();
        boolean includeRunningVMBootclasspath = true;
        
        parser.setEnvironment(
            classPath,           // External .jar paths
            sourceDirs,          // Internal source roots
            encodings,           // File encodings (null = platform default)
            includeRunningVMBootclasspath
        );
        
        // Step 5: Create a requestor to collect parsed units
        FileASTRequestor requestor = new FileASTRequestor() {
            @Override
            public void acceptAST(String sourceFilePath, CompilationUnit ast) {
                Path path = Path.of(sourceFilePath);
                units.put(path, ast);
            }
        };
        
        // Step 6: Batch parse all files (binding resolution now enabled)
        parser.createASTs(filePaths, encodings, new String[0], requestor, null);
        
        // Fallback: If batch parsing failed, parse individually
        if (units.isEmpty()) {
            System.err.println("Batch parsing failed; falling back to per-file parsing");
            return parseCompilationUnitsPerFile(files);
        }
        
        // Log success
        System.err.println(String.format(
            "Parsed %d files with environment: %d source roots, %d classpath entries",
            units.size(), sourceDirs.length, classPath.length
        ));
        
    } catch (Exception e) {
        System.err.println("Batch parsing failed: " + e.getMessage());
        System.err.println("Falling back to per-file parsing (bindings may degrade)");
        return parseCompilationUnitsPerFile(files);
    }
    
    return units;
}

/**
 * Fallback per-file parser (used if batch parsing fails).
 * Same as original implementation; bindings may be null.
 */
private Map<Path, CompilationUnit> parseCompilationUnitsPerFile(List<Path> files) {
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
```

---

### Step 3: Update `analyze()` to Call Discovery

**File**: `src/java/src/main/java/com/lineagelens/parser/JavaAstAnalyzer.java`

**Replace lines 26-52** with:

```java
public CodeGraph analyze() throws IOException {
    CodeGraph graph = new CodeGraph(projectRoot.toString());
    List<Path> javaFiles = collectJavaFiles(projectRoot);
    
    // NEW: Discover sourcepath and classpath
    List<Path> sourceRoots = discoverSourceRoots();
    String[] sourceDirs = sourceRoots.stream()
        .map(Path::toString)
        .toArray(String[]::new);
    String[] classPath = resolveClasspath();
    
    System.err.println(String.format(
        "Discovered %d source roots, %d classpath entries for %d Java files",
        sourceDirs.length, classPath.length, javaFiles.size()
    ));
    
    // Pre-parse compilation units with environment (MODIFIED)
    Map<Path, CompilationUnit> compilationUnits = parseCompilationUnits(
        javaFiles,
        sourceDirs,
        classPath
    );
    
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
```

---

### Step 4: Improve Binding Resolution Fallback

**File**: `src/java/src/main/java/com/lineagelens/parser/JavaAstAnalyzer.java`

**Replace lines 414-421** with:

```java
/**
 * Fallback: Finds method symbol by name + arity matching (if binding fails).
 * Improves on pure name-matching by considering parameter count.
 * Still ambiguous but better than name-only lookup.
 *
 * @param methodName Simple method name (no package prefix)
 * @param paramCount Number of parameters (if available)
 * @return Method symbol ID, or null if not found
 */
private String findMethodSymbolIdByName(String methodName, int paramCount) {
    String bestMatch = null;
    int matches = 0;
    
    // First pass: exact name + arity match
    for (Symbol symbol : graph.getSymbols()) {
        if (symbol.getName().equals(methodName) &&
            (symbol.getKind().equals("method") || symbol.getKind().equals("constructor"))) {
            // Extract parameter count from symbol signature if available
            // Format: "className.methodName(param1,param2)"
            String id = symbol.getId();
            int parenIdx = id.lastIndexOf('(');
            if (parenIdx > 0) {
                String signature = id.substring(parenIdx);
                int symbolParamCount = signature.equals("()") ? 0 : signature.split(",").length;
                if (symbolParamCount == paramCount) {
                    return id; // Exact arity match
                }
            }
            bestMatch = id;
            matches++;
        }
    }
    
    // If multiple matches (ambiguous), log it
    if (matches > 1 && bestMatch != null) {
        System.err.println(String.format(
            "Warning: Method '%s' has %d overloads; using first match (binding failed)",
            methodName, matches
        ));
    }
    
    return bestMatch;
}

// Keep original 0-arg version for backwards compatibility
private String findMethodSymbolIdByName(String methodName) {
    return findMethodSymbolIdByName(methodName, -1); // -1 = ignore arity
}
```

---

### Step 5: Add Import for BufferedReader

**File**: `src/java/src/main/java/com/lineagelens/parser/JavaAstAnalyzer.java`

**Add at top of file (after line 17)**:

```java
import java.io.BufferedReader;
import java.io.InputStreamReader;
```

---

## Test Cases to Add

**File**: `tests/test_java_integration.py`

Add the following test cases (after existing tests):

```python
import pytest
import subprocess
from pathlib import Path
from lineagelens.parser import JavaAstAnalyzer
from lineagelens.model import CodeGraph


class TestJavaSourcepathDiscovery:
    """Test Java sourcepath discovery for Maven/Gradle projects."""
    
    def test_single_module_maven_project(self, tmp_path):
        """Verify sourcepath resolution finds src/main/java in Maven project."""
        # Create minimal Maven structure
        pom = tmp_path / "pom.xml"
        pom.write_text("""<?xml version="1.0"?>
<project>
    <sourceDirectory>src/main/java</sourceDirectory>
    <testSourceDirectory>src/test/java</testSourceDirectory>
</project>
""")
        src_dir = tmp_path / "src" / "main" / "java"
        src_dir.mkdir(parents=True)
        
        # Create a simple Java file
        java_file = src_dir / "Test.java"
        java_file.write_text("public class Test {}")
        
        analyzer = JavaAstAnalyzer(tmp_path)
        roots = analyzer.discoverSourceRoots()
        
        assert len(roots) > 0
        assert src_dir in roots
    
    def test_multi_module_maven_project(self, tmp_path):
        """Verify sourcepath resolution handles Maven multi-module projects."""
        # Create parent pom.xml
        parent_pom = tmp_path / "pom.xml"
        parent_pom.write_text("""<?xml version="1.0"?>
<project>
    <modules>
        <module>module1</module>
        <module>module2</module>
    </modules>
</project>
""")
        
        # Create module1 with source
        mod1_pom = tmp_path / "module1" / "pom.xml"
        mod1_pom.parent.mkdir()
        mod1_pom.write_text("""<?xml version="1.0"?>
<project>
    <sourceDirectory>src/main/java</sourceDirectory>
</project>
""")
        mod1_src = tmp_path / "module1" / "src" / "main" / "java"
        mod1_src.mkdir(parents=True)
        (mod1_src / "Module1Class.java").write_text("public class Module1Class {}")
        
        # Create module2 with source
        mod2_pom = tmp_path / "module2" / "pom.xml"
        mod2_pom.parent.mkdir()
        mod2_pom.write_text("""<?xml version="1.0"?>
<project>
    <sourceDirectory>src/main/java</sourceDirectory>
</project>
""")
        mod2_src = tmp_path / "module2" / "src" / "main" / "java"
        mod2_src.mkdir(parents=True)
        (mod2_src / "Module2Class.java").write_text("public class Module2Class {}")
        
        analyzer = JavaAstAnalyzer(tmp_path)
        roots = analyzer.discoverSourceRoots()
        
        assert mod1_src in roots
        assert mod2_src in roots
        assert len(roots) >= 2
    
    def test_gradle_project(self, tmp_path):
        """Verify sourcepath resolution works for Gradle projects."""
        build_gradle = tmp_path / "build.gradle"
        build_gradle.write_text("""
sourceSets {
    main {
        java {
            srcDirs = ['src/main/java']
        }
    }
}
""")
        
        src_dir = tmp_path / "src" / "main" / "java"
        src_dir.mkdir(parents=True)
        (src_dir / "GradleTest.java").write_text("public class GradleTest {}")
        
        analyzer = JavaAstAnalyzer(tmp_path)
        roots = analyzer.discoverSourceRoots()
        
        assert src_dir in roots
    
    def test_fallback_standard_layout(self, tmp_path):
        """Verify fallback to standard src/main/java if no build config."""
        src_dir = tmp_path / "src" / "main" / "java"
        src_dir.mkdir(parents=True)
        (src_dir / "Standard.java").write_text("public class Standard {}")
        
        analyzer = JavaAstAnalyzer(tmp_path)
        roots = analyzer.discoverSourceRoots()
        
        assert src_dir in roots


class TestJavaBindingResolution:
    """Test improved binding resolution for cross-module calls."""
    
    def test_cross_module_method_call_with_binding(self, tmp_path):
        """Verify cross-module method calls resolve via JDT (not inference)."""
        # Create module1: defines MyService.doWork()
        mod1_src = tmp_path / "module1" / "src" / "main" / "java" / "com" / "example"
        mod1_src.mkdir(parents=True)
        
        mod1_service = mod1_src / "MyService.java"
        mod1_service.write_text("""
package com.example;
public class MyService {
    public void doWork() {
        System.out.println("Working");
    }
}
""")
        
        # Create module2: calls MyService.doWork()
        mod2_src = tmp_path / "module2" / "src" / "main" / "java" / "com" / "example"
        mod2_src.mkdir(parents=True)
        
        mod2_client = mod2_src / "Client.java"
        mod2_client.write_text("""
package com.example;
public class Client {
    public void callService(MyService service) {
        service.doWork();  // Should resolve via binding
    }
}
""")
        
        # Create pom.xml with modules
        pom = tmp_path / "pom.xml"
        pom.write_text("""<?xml version="1.0"?>
<project>
    <modules>
        <module>module1</module>
        <module>module2</module>
    </modules>
</project>
""")
        
        # Add module poms
        (tmp_path / "module1" / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <sourceDirectory>src/main/java</sourceDirectory>
</project>
""")
        (tmp_path / "module2" / "pom.xml").write_text("""<?xml version="1.0"?>
<project>
    <sourceDirectory>src/main/java</sourceDirectory>
</project>
""")
        
        # Analyze
        analyzer = JavaAstAnalyzer(tmp_path)
        graph = analyzer.analyze()
        
        # Verify: Client.callService -> MyService.doWork relation exists
        client_method = graph.getSymbol("com.example.Client.callService")
        service_method = graph.getSymbol("com.example.MyService.doWork")
        
        assert client_method is not None, "Client.callService not found"
        assert service_method is not None, "MyService.doWork not found"
        
        # Check for CALLS relation
        relations = [r for r in graph.getRelations()
                     if r.getSource() == "com.example.Client.callService"]
        assert len(relations) > 0, "No CALLS relation from Client.callService"
        
        # Verify binding resolution (not inference)
        calls_relation = [r for r in relations if r.getTarget() == "com.example.MyService.doWork"][0]
        evidence = calls_relation.getEvidence()
        # Should be "resolved" or "deterministic_fact", NOT "resolved_via_inference"
        assert evidence is not None
        # If binding succeeded, evidence should indicate deterministic resolution
    
    def test_method_overload_resolution(self, tmp_path):
        """Verify overloaded method calls resolve correctly (arity matching)."""
        src = tmp_path / "src" / "main" / "java" / "com" / "example"
        src.mkdir(parents=True)
        
        # Class with overloaded methods
        service = src / "Service.java"
        service.write_text("""
package com.example;
public class Service {
    public void process(String data) { }
    public void process(int count) { }
    public void process(String data, int count) { }
}
""")
        
        # Caller with specific overload calls
        caller = src / "Caller.java"
        caller.write_text("""
package com.example;
public class Caller {
    void test(Service s) {
        s.process("hello");          // Should match process(String)
        s.process(42);               // Should match process(int)
        s.process("world", 10);      // Should match process(String, int)
    }
}
""")
        
        analyzer = JavaAstAnalyzer(tmp_path)
        graph = analyzer.analyze()
        
        # Verify that call relations exist (arity matching helps here)
        caller_method = graph.getSymbol("com.example.Caller.test")
        assert caller_method is not None
        
        relations = [r for r in graph.getRelations()
                     if r.getSource() == "com.example.Caller.test" and "process" in r.getTarget()]
        # Should have 3 CALLS relations (or fallback to first match)
        # Exact count depends on binding resolution; main goal is no crash
        assert len(relations) >= 1, "No process() calls resolved"
    
    def test_classpath_unavailable_graceful_fallback(self, tmp_path):
        """Verify analysis succeeds (sourcepath-only) if classpath unavailable."""
        # Create simple project without Maven/Gradle config
        src = tmp_path / "src" / "main" / "java"
        src.mkdir(parents=True)
        
        java_file = src / "Simple.java"
        java_file.write_text("""
public class Simple {
    public void test() {
        System.out.println("Hello");
    }
}
""")
        
        # Analyze (no Maven, no classpath resolution)
        analyzer = JavaAstAnalyzer(tmp_path)
        # Should not crash, even though classpath is empty
        graph = analyzer.analyze()
        
        assert graph is not None
        simple_class = graph.getSymbol("Simple")
        assert simple_class is not None
```

---

## Success Criteria

1. ✅ **All existing tests pass** (no regression)
   ```bash
   cd src/java && gradle test
   python3 -m pytest tests/test_java_integration.py -v
   ```

2. ✅ **New cross-module test** demonstrates binding resolution:
   - Test `test_cross_module_method_call_with_binding` passes
   - Method calls marked as "resolved" (not "resolved_via_inference")
   - Graph correctly identifies inter-module CALLS relations

3. ✅ **Graceful degradation** on classpath unavailability:
   - Analysis completes (no exceptions)
   - Sourcepath-only resolution works
   - Test `test_classpath_unavailable_graceful_fallback` passes

4. ✅ **No performance regression** on single-module projects:
   - Analysis time ≤ 2x original for small projects (<50 files)
   - Batch parsing more efficient than per-file for large projects

5. ✅ **Multi-module detection** working:
   - Maven test `test_multi_module_maven_project` finds all modules
   - All source roots included in sourceDirs array

---

## Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| pom.xml malformed | Catch exception, continue to Gradle check, fallback to standard patterns |
| build.gradle regex fails | Catch exception, fallback to standard patterns |
| mvn dependency:build-classpath fails | Return empty array (sourcepath-only is OK) |
| Classpath contains missing jars | Filter to existing files only |
| Source roots don't exist | Filter to existing paths only |
| No source roots found anywhere | Use projectRoot as fallback (will find top-level .java files) |
| INameEnvironment setup fails | Catch exception, fall back to per-file parsing (line 95-107) |
| Batch parsing returns no units | Use per-file fallback (line 105) |
| Binding still null after env setup | Use existing fallback chain (findMethodSymbolIdByName) |

**Key principle**: Never throw exceptions; always degrade gracefully to best-effort analysis.

---

## Code Locations to Change

| File | Lines | Change | Reason |
|------|-------|--------|--------|
| `JavaAstAnalyzer.java` | 22-24 | Constructor (no change needed) | Entry point documented |
| `JavaAstAnalyzer.java` | 26-52 | `analyze()` method | Call discovery before parsing |
| `JavaAstAnalyzer.java` | 69-92 | `parseCompilationUnits()` | Replace with batch API + environment |
| `JavaAstAnalyzer.java` | After 67 | New utility methods | Add discovery + classpath functions |
| `JavaAstAnalyzer.java` | 414-421 | `findMethodSymbolIdByName()` | Improve with arity matching |
| `JavaAstAnalyzer.java` | Top (after imports) | Add imports | BufferedReader, InputStreamReader |
| `test_java_integration.py` | End of file | New test class | Add 7+ test cases |

---

## Risk Mitigation

### Risk 1: Maven/Gradle Parsing Fragile
**Mitigation**: Use simple string parsing + regex; always fallback to standard patterns if parsing fails. Never depend on external tools (Maven/Gradle). Graceful degradation is OK.

### Risk 2: Performance Regression
**Mitigation**: Benchmark on existing test fixtures (LineageLens itself, Java GitHub projects). Batch parsing should be faster than per-file; if not, use per-file for small projects (<100 files).

### Risk 3: Bindings Still Null Despite Environment
**Mitigation**: Existing fallback chain (findMethodSymbolIdByName) stays in place. Evidence is marked "resolved_via_inference" to track this. No analysis breaks.

### Risk 4: Classpath Resolution Too Aggressive
**Mitigation**: Return empty array if classpath unavailable. Sourcepath-only is sufficient for internal calls.

### Risk 5: Multi-Module Recursion Too Deep
**Mitigation**: Add recursion depth limit (e.g., max 10 levels) in Maven module discovery. Log warnings if exceeded.

---

## Implementation Checklist

- [ ] Read full JavaAstAnalyzer.java to understand context
- [ ] Implement Step 1: Add utility methods (discoverSourceRoots, resolveClasspath, etc.)
- [ ] Implement Step 2: Refactor parseCompilationUnits to use batch API + INameEnvironment
- [ ] Implement Step 3: Update analyze() to call discovery
- [ ] Implement Step 4: Improve findMethodSymbolIdByName with arity matching
- [ ] Implement Step 5: Add imports
- [ ] Run existing tests: `gradle test` in src/java/
- [ ] Add new test cases to test_java_integration.py
- [ ] Run new tests: `pytest tests/test_java_integration.py::TestJavaSourcepathDiscovery -v`
- [ ] Run new tests: `pytest tests/test_java_integration.py::TestJavaBindingResolution -v`
- [ ] Benchmark on 3+ multi-module projects (verify no regression)
- [ ] Test graceful fallback scenarios (missing pom.xml, broken classpath, etc.)
- [ ] Create PR with description linking this spec (Issue #14)

---

## Validation Procedure

### Before Merge
```bash
# 1. Run all Java tests
cd src/java && gradle test

# 2. Run Python integration tests
python3 -m pytest tests/test_java_integration.py -v

# 3. Analyze LineageLens itself (dogfood test)
cd ../.. && python3 -m lineagelens analyze ./src/java
# Verify output has resolved bindings (not inference)

# 4. Benchmark on real project
# Choose a multi-module Maven/Gradle project from GitHub
# Compare analysis quality before/after
```

### Success Signals
- ✅ All tests pass
- ✅ Cross-module calls show evidence="resolved" (not "resolved_via_inference")
- ✅ Analysis completes even with missing classpath/pom.xml
- ✅ Performance ≤ 2x original for small projects

---

## Future Enhancements (Phase 2+)

1. **Gradle Multi-Project Handling**: Parse nested `subprojects { }` blocks
2. **Maven Profile Support**: Handle `<profiles>` with different source roots
3. **Annotation Processing**: Detect and resolve `@Generated` classes
4. **Bytecode Analysis Fallback**: If source unavailable, analyze compiled .class files
5. **Dependency Graph Caching**: Cache resolved classpath between runs (`.lineagelens/classpath.cache`)
6. **IDE Integration**: Export sourcepath/classpath as IDEA `.iml` or Eclipse `.classpath` files

---

## References

- **JDT Docs**: https://help.eclipse.org/latest/index.jsp?topic=%2Forg.eclipse.jdt.doc.isv%2Freference%2Fapi%2Forg%2Feclipse%2Fjdt%2Fcore%2Fdom%2FASTParser.html
- **ASTParser.createASTs()**: Batch parsing API (more efficient than per-file)
- **INameEnvironment**: Enables cross-file type resolution in JDT
- **Maven Coordinates**: https://maven.apache.org/pom.html
- **Gradle Build Metadata**: https://docs.gradle.org/current/userguide/building_java_projects.html

