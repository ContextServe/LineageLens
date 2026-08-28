package com.lineagelens.parser;

import com.lineagelens.model.Symbol;
import org.eclipse.jdt.core.dom.AST;
import org.eclipse.jdt.core.dom.ASTParser;
import org.eclipse.jdt.core.dom.CompilationUnit;
import org.eclipse.jdt.core.dom.MethodInvocation;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

public class JavaResiliencyAnalyzerTest {

    private MethodInvocation parseInvocation(String source) {
        ASTParser parser = ASTParser.newParser(AST.JLS17);
        parser.setSource(source.toCharArray());
        parser.setKind(ASTParser.K_COMPILATION_UNIT);
        CompilationUnit cu = (CompilationUnit) parser.createAST(null);
        org.eclipse.jdt.core.dom.TypeDeclaration type = (org.eclipse.jdt.core.dom.TypeDeclaration) cu.types().get(0);
        org.eclipse.jdt.core.dom.MethodDeclaration method = type.getMethods()[0];
        org.eclipse.jdt.core.dom.ExpressionStatement stmt = (org.eclipse.jdt.core.dom.ExpressionStatement) method.getBody().statements().get(0);
        return (MethodInvocation) stmt.getExpression();
    }

    @Test
    public void testDataWriteInvocation() {
        String code = """
                public class Service {
                    public void run() {
                        repo.save(entity);
                    }
                }
                """;
        MethodInvocation inv = parseInvocation(code);
        Symbol symbol = new Symbol("Service.run", "method", "run", "Service.java", 2, "Service", null);

        JavaResiliencyAnalyzer.checkMethodInvocation(inv, symbol, 3);
        assertEquals(1, symbol.getResiliency().size());
        assertEquals("data_write", symbol.getResiliency().get(0).getCategory());
        assertEquals("review", symbol.getResiliency().get(0).getSeverity());
    }

    @Test
    public void testBlockingOperationInAsync() {
        String code = """
                public class AsyncService {
                    public void run() throws Exception {
                        Thread.sleep(1000);
                    }
                }
                """;
        MethodInvocation inv = parseInvocation(code);

        Symbol syncSymbol = new Symbol("AsyncService.run", "method", "run", "AsyncService.java", 2, "AsyncService", null);
        JavaResiliencyAnalyzer.checkMethodInvocation(inv, syncSymbol, 3);
        assertEquals(1, syncSymbol.getResiliency().size());
        assertEquals("blocking_operation", syncSymbol.getResiliency().get(0).getCategory());

        Symbol asyncSymbol = new Symbol("AsyncService.run", "method", "run", "AsyncService.java", 2, "AsyncService", null);
        asyncSymbol.setAsync_(true);
        JavaResiliencyAnalyzer.checkMethodInvocation(inv, asyncSymbol, 3);
        assertEquals(1, asyncSymbol.getResiliency().size());
        assertEquals("blocking_in_async", asyncSymbol.getResiliency().get(0).getCategory());
        assertEquals("high", asyncSymbol.getResiliency().get(0).getSeverity());
    }
}
