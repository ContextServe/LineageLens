package com.lineagelens.model;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class CodeGraph {
    public static final int SCHEMA_VERSION = 2;

    @JsonProperty("schema_version")
    private int schemaVersion = SCHEMA_VERSION;

    @JsonProperty("project_root")
    private String projectRoot;

    @JsonProperty("symbols")
    private List<Symbol> symbolsList = new ArrayList<>();

    @JsonProperty("containers")
    private List<Container> containersList = new ArrayList<>();

    @JsonProperty("relations")
    private List<Relation> relations = new ArrayList<>();

    // Internal lookup maps
    private final Map<String, Symbol> symbolsMap = new LinkedHashMap<>();
    private final Map<String, Container> containersMap = new LinkedHashMap<>();

    public CodeGraph() {}

    public CodeGraph(String projectRoot) {
        this.projectRoot = projectRoot;
    }

    public void addSymbol(Symbol symbol) {
        symbolsMap.put(symbol.getId(), symbol);
    }

    public void addContainer(Container container) {
        containersMap.put(container.getId(), container);
    }

    public void addRelation(Relation relation) {
        relations.add(relation);
    }

    public Symbol getSymbol(String id) {
        return symbolsMap.get(id);
    }

    public Container getContainer(String id) {
        return containersMap.get(id);
    }

    public Collection<Symbol> getSymbols() {
        return symbolsMap.values();
    }

    public Collection<Container> getContainers() {
        return containersMap.values();
    }

    public List<Relation> getRelations() {
        return relations;
    }

    public int getSchemaVersion() {
        return schemaVersion;
    }

    public String getProjectRoot() {
        return projectRoot;
    }

    public void prepareForSerialization() {
        this.symbolsList = new ArrayList<>(symbolsMap.values());
        this.containersList = new ArrayList<>(containersMap.values());
    }
}
