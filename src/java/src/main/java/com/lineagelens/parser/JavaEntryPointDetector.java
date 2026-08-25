package com.lineagelens.parser;

import com.lineagelens.model.Symbol;
import org.eclipse.jdt.core.dom.IAnnotationBinding;
import org.eclipse.jdt.core.dom.IMethodBinding;
import org.eclipse.jdt.core.dom.MethodDeclaration;
import org.eclipse.jdt.core.dom.Modifier;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

public class JavaEntryPointDetector {

    private static final Set<String> API_ROUTE_ANNOTATIONS = Set.of(
            "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping",
            "Get", "Post", "Put", "Delete", "Patch", "Path", "HEAD", "OPTIONS"
    );

    private static final Set<String> TEST_ANNOTATIONS = Set.of(
            "Test", "ParameterizedTest", "RepeatedTest", "TestFactory"
    );

    private static final Set<String> TEST_FIXTURE_ANNOTATIONS = Set.of(
            "BeforeEach", "AfterEach", "BeforeAll", "AfterAll", "Before", "After", "BeforeClass", "AfterClass"
    );

    private static final Set<String> TASK_ANNOTATIONS = Set.of(
            "Scheduled", "ScheduledMethod"
    );

    private static final Set<String> CALLBACK_ANNOTATIONS = Set.of(
            "PostConstruct", "PreDestroy", "EventListener"
    );

    public static List<String> detectEntryPoints(MethodDeclaration node, Symbol symbol) {
        List<String> entryKinds = new ArrayList<>();

        // Check if main method: public static void main(String[] args)
        if ("main".equals(node.getName().getIdentifier()) && Modifier.isPublic(node.getModifiers()) && Modifier.isStatic(node.getModifiers())) {
            entryKinds.add("main_module");
        }

        // Check annotations
        IMethodBinding methodBinding = node.resolveBinding();
        if (methodBinding != null) {
            for (IAnnotationBinding annotation : methodBinding.getAnnotations()) {
                String name = annotation.getName();
                if (API_ROUTE_ANNOTATIONS.contains(name)) {
                    entryKinds.add("api_route");
                }
                if (TEST_ANNOTATIONS.contains(name)) {
                    entryKinds.add("test");
                }
                if (TEST_FIXTURE_ANNOTATIONS.contains(name)) {
                    entryKinds.add("test_fixture");
                }
                if (TASK_ANNOTATIONS.contains(name)) {
                    entryKinds.add("task");
                }
                if (CALLBACK_ANNOTATIONS.contains(name)) {
                    entryKinds.add("framework_callback");
                }
            }
        } else {
            // Fallback: check AST annotation names directly if bindings are unavailable
            for (Object modifierObj : node.modifiers()) {
                if (modifierObj instanceof org.eclipse.jdt.core.dom.Annotation ann) {
                    String name = ann.getTypeName().getFullyQualifiedName();
                    String simpleName = name.contains(".") ? name.substring(name.lastIndexOf('.') + 1) : name;
                    if (API_ROUTE_ANNOTATIONS.contains(simpleName) && !entryKinds.contains("api_route")) {
                        entryKinds.add("api_route");
                    }
                    if (TEST_ANNOTATIONS.contains(simpleName) && !entryKinds.contains("test")) {
                        entryKinds.add("test");
                    }
                    if (TEST_FIXTURE_ANNOTATIONS.contains(simpleName) && !entryKinds.contains("test_fixture")) {
                        entryKinds.add("test_fixture");
                    }
                    if (TASK_ANNOTATIONS.contains(simpleName) && !entryKinds.contains("task")) {
                        entryKinds.add("task");
                    }
                    if (CALLBACK_ANNOTATIONS.contains(simpleName) && !entryKinds.contains("framework_callback")) {
                        entryKinds.add("framework_callback");
                    }
                }
            }
        }

        return entryKinds;
    }
}
