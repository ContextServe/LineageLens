package com.lineagelens.model;

import com.fasterxml.jackson.annotation.JsonProperty;

public class ResiliencySignal {
    @JsonProperty("category")
    private String category;

    @JsonProperty("severity")
    private String severity; // "info", "review", "high"

    @JsonProperty("evidence")
    private Evidence evidence;

    @JsonProperty("line")
    private int line;

    public ResiliencySignal() {}

    public ResiliencySignal(String category, String severity, Evidence evidence, int line) {
        this.category = category;
        this.severity = severity;
        this.evidence = evidence;
        this.line = line;
    }

    public String getCategory() {
        return category;
    }

    public String getSeverity() {
        return severity;
    }

    public Evidence getEvidence() {
        return evidence;
    }

    public int getLine() {
        return line;
    }
}
