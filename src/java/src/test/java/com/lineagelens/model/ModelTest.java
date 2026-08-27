package com.lineagelens.model;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

public class ModelTest {

    @Test
    public void testEvidenceModel() {
        Evidence ev1 = new Evidence("deterministic_fact", "static_ast");
        assertEquals("deterministic_fact", ev1.getTier());
        assertEquals("static_ast", ev1.getLabel());
        assertNull(ev1.getConfidence());

        Evidence ev2 = new Evidence("probabilistic", "llm", 0.95);
        assertEquals("probabilistic", ev2.getTier());
        assertEquals("llm", ev2.getLabel());
        assertEquals(0.95, ev2.getConfidence());

        Evidence fact = Evidence.fact("test_fact");
        assertEquals("deterministic_fact", fact.getTier());

        Evidence heuristic = Evidence.heuristic("test_heuristic");
        assertEquals("deterministic_heuristic", heuristic.getTier());

        ev1.setTier("deterministic_heuristic");
        ev1.setLabel("new_label");
        ev1.setConfidence(0.8);
        assertEquals("deterministic_heuristic", ev1.getTier());
        assertEquals("new_label", ev1.getLabel());
        assertEquals(0.8, ev1.getConfidence());
    }

    @Test
    public void testResiliencySignalModel() {
        Evidence ev = Evidence.heuristic("rule match");
        ResiliencySignal signal = new ResiliencySignal("data_write", "review", ev, 15);

        assertEquals("data_write", signal.getCategory());
        assertEquals("review", signal.getSeverity());
        assertEquals(ev, signal.getEvidence());
        assertEquals(15, signal.getLine());
    }

    @Test
    public void testContainerModel() {
        Container container = new Container("pkg.sub", "package", "sub", "pkg/sub", "pkg");
        assertEquals("pkg.sub", container.getId());
        assertEquals("package", container.getKind());
        assertEquals("sub", container.getName());
        assertEquals("pkg/sub", container.getFile());
        assertEquals("pkg", container.getParent());
        assertTrue(container.getChildren().isEmpty());

        container.addChild("pkg.sub.MyClass");
        container.addChild("pkg.sub.MyClass"); // Test deduplication
        assertEquals(1, container.getChildren().size());
        assertEquals("pkg.sub.MyClass", container.getChildren().get(0));

        container.setDocstring("Package doc");
        assertEquals("Package doc", container.getDocstring());
    }

    @Test
    public void testSymbolModel() {
        Symbol symbol = new Symbol("com.example.Foo.bar", "method", "bar", "com/example/Foo.java", 10, "com.example", "com.example.Foo");
        assertEquals("com.example.Foo.bar", symbol.getId());
        assertEquals("method", symbol.getKind());
        assertEquals("bar", symbol.getName());
        assertEquals("com/example/Foo.java", symbol.getFile());
        assertEquals(10, symbol.getLine());
        assertEquals("com.example", symbol.getModule());
        assertEquals("com.example.Foo", symbol.getParent());

        symbol.setEndLine(25);
        assertEquals(25, symbol.getEndLine());

        symbol.setAsync_(true);
        assertTrue(symbol.isAsync_());

        symbol.setDescription("Method description");
        assertEquals("Method description", symbol.getDescription());

        symbol.addInput("id", "String", "null");
        assertEquals(1, symbol.getInputs().size());
        Map<String, Object> input = symbol.getInputs().get(0);
        assertEquals("id", input.get("name"));
        assertEquals("String", input.get("type"));

        symbol.addOutput("void", "annotation");
        assertEquals(1, symbol.getOutputs().size());

        symbol.addDecorator("GetMapping");
        symbol.addDecorator("GetMapping"); // Deduplication
        assertEquals(1, symbol.getDecorators().size());

        symbol.addBase("BaseClass");
        symbol.addBase("BaseClass"); // Deduplication
        assertEquals(1, symbol.getBases().size());

        symbol.markEntryPoint("api_route");
        symbol.markEntryPoint("api_route"); // Deduplication
        assertEquals("api_route", symbol.getEntryPoint());
        assertEquals(1, symbol.getEntryPointKinds().size());

        symbol.setAbstract(true);
        assertTrue(symbol.isAbstract());

        ResiliencySignal sig = new ResiliencySignal("data_write", "review", Evidence.heuristic("save"), 12);
        symbol.addResiliencySignal(sig);
        assertEquals(1, symbol.getResiliency().size());
    }

    @Test
    public void testRelationModel() {
        Relation rel = new Relation("src", "target", "CALLS", "file.java", 10);
        assertEquals("src", rel.getSource());
        assertEquals("target", rel.getTarget());
        assertEquals("CALLS", rel.getKind());
        assertEquals("file.java", rel.getFile());
        assertEquals(10, rel.getLine());
        assertEquals("deterministic_fact", rel.getEvidence().getTier());
        assertEquals("resolved", rel.getResolution());

        Evidence ev2 = Evidence.heuristic("inferred");
        rel.setResolution("resolved_via_inference", ev2);
        assertEquals("resolved_via_inference", rel.getResolution());
        assertEquals(ev2, rel.getResolutionEvidence());

        Relation relFull = new Relation("src", "target", "INHERITS", "file.java", 5, Evidence.fact("ast"), "resolved", Evidence.fact("scope"));
        assertEquals("INHERITS", relFull.getKind());
        assertTrue(relFull.getArguments().isEmpty());
    }

    @Test
    public void testCodeGraphModel() {
        CodeGraph graph = new CodeGraph("/root");
        assertEquals(2, graph.getSchemaVersion());
        assertEquals("/root", graph.getProjectRoot());

        Symbol sym = new Symbol("sym1", "class", "sym1", "file.java", 1, "mod", null);
        Container c = new Container("c1", "package", "c1", null, null);
        Relation r = new Relation("sym1", "sym2", "CALLS", "file.java", 5);

        graph.addSymbol(sym);
        graph.addContainer(c);
        graph.addRelation(r);

        assertEquals(sym, graph.getSymbol("sym1"));
        assertEquals(c, graph.getContainer("c1"));
        assertEquals(1, graph.getSymbols().size());
        assertEquals(1, graph.getContainers().size());
        assertEquals(1, graph.getRelations().size());

        graph.prepareForSerialization();
    }
}
