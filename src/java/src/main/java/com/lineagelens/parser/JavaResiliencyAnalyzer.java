package com.lineagelens.parser;

import com.lineagelens.model.Evidence;
import com.lineagelens.model.ResiliencySignal;
import com.lineagelens.model.Symbol;
import org.eclipse.jdt.core.dom.MethodInvocation;

import java.util.Set;

public class JavaResiliencyAnalyzer {

    private static final Set<String> DATA_WRITE_METHODS = Set.of(
            "save", "saveAll", "saveAndFlush", "delete", "deleteAll", "deleteById",
            "update", "insert", "execute", "executeUpdate", "persist", "remove", "merge"
    );

    private static final Set<String> BLOCKING_METHODS = Set.of(
            "sleep", "wait", "join", "readLine", "read", "accept"
    );

    public static void checkMethodInvocation(MethodInvocation node, Symbol symbol, int line) {
        String methodName = node.getName().getIdentifier();

        if (DATA_WRITE_METHODS.contains(methodName)) {
            symbol.addResiliencySignal(new ResiliencySignal(
                    "data_write",
                    "review",
                    Evidence.heuristic("rule match: " + methodName + " at line " + line),
                    line
            ));
        }

        if (BLOCKING_METHODS.contains(methodName)) {
            String category = symbol.isAsync_() ? "blocking_in_async" : "blocking_operation";
            String severity = symbol.isAsync_() ? "high" : "review";
            symbol.addResiliencySignal(new ResiliencySignal(
                    category,
                    severity,
                    Evidence.heuristic("rule match: " + methodName + " at line " + line),
                    line
            ));
        }
    }
}
