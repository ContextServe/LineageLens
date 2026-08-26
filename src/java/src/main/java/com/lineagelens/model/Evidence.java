package com.lineagelens.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class Evidence {
    @JsonProperty("tier")
    private String tier; // "deterministic_fact", "deterministic_heuristic", "probabilistic"

    @JsonProperty("label")
    private String label;

    @JsonProperty("confidence")
    private Double confidence;

    public Evidence() {}

    public Evidence(String tier, String label) {
        this.tier = tier;
        this.label = label;
        this.confidence = null;
    }

    public Evidence(String tier, String label, Double confidence) {
        this.tier = tier;
        this.label = label;
        this.confidence = confidence;
    }

    public static Evidence fact(String label) {
        return new Evidence("deterministic_fact", label);
    }

    public static Evidence heuristic(String label) {
        return new Evidence("deterministic_heuristic", label);
    }

    public String getTier() {
        return tier;
    }

    public void setTier(String tier) {
        this.tier = tier;
    }

    public String getLabel() {
        return label;
    }

    public void setLabel(String label) {
        this.label = label;
    }

    public Double getConfidence() {
        return confidence;
    }

    public void setConfidence(Double confidence) {
        this.confidence = confidence;
    }
}
