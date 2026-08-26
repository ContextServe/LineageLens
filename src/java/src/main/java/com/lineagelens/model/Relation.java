package com.lineagelens.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class Relation {
    @JsonProperty("source")
    private String source;

    @JsonProperty("target")
    private String target;

    @JsonProperty("kind")
    private String kind; // "CALLS", "AWAIT_CALLS", "INHERITS", "DECORATES", "OVERRIDES", "ANNOTATES"

    @JsonProperty("file")
    private String file;

    @JsonProperty("line")
    private int line;

    @JsonProperty("evidence")
    private Evidence evidence = Evidence.fact("static_ast");

    @JsonProperty("resolution")
    private String resolution = "resolved"; // "resolved", "resolved_via_inference", "external_or_dynamic"

    @JsonProperty("resolution_evidence")
    private Evidence resolutionEvidence = Evidence.fact("static_scope_walk");

    @JsonProperty("arguments")
    private List<Map<String, Object>> arguments = new ArrayList<>();

    public Relation() {}

    public Relation(String source, String target, String kind, String file, int line) {
        this.source = source;
        this.target = target;
        this.kind = kind;
        this.file = file;
        this.line = line;
    }

    public Relation(String source, String target, String kind, String file, int line, Evidence evidence, String resolution, Evidence resolutionEvidence) {
        this.source = source;
        this.target = target;
        this.kind = kind;
        this.file = file;
        this.line = line;
        this.evidence = evidence;
        this.resolution = resolution;
        this.resolutionEvidence = resolutionEvidence;
    }

    public String getSource() {
        return source;
    }

    public String getTarget() {
        return target;
    }

    public String getKind() {
        return kind;
    }

    public String getFile() {
        return file;
    }

    public int getLine() {
        return line;
    }

    public Evidence getEvidence() {
        return evidence;
    }

    public String getResolution() {
        return resolution;
    }

    public Evidence getResolutionEvidence() {
        return resolutionEvidence;
    }

    public List<Map<String, Object>> getArguments() {
        return arguments;
    }

    public void setResolution(String resolution, Evidence resolutionEvidence) {
        this.resolution = resolution;
        this.resolutionEvidence = resolutionEvidence;
    }
}
