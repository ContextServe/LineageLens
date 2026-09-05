package com.lineagelens.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class Symbol {
    @JsonProperty("id")
    private String id;

    @JsonProperty("kind")
    private String kind; // "class", "interface", "enum", "function", "method", "constructor"

    @JsonProperty("name")
    private String name;

    @JsonProperty("file")
    private String file;

    @JsonProperty("line")
    private int line;

    @JsonProperty("end_line")
    private Integer endLine;

    @JsonProperty("module")
    private String module;

    @JsonProperty("parent")
    private String parent;

    @JsonProperty("async_")
    private boolean async_ = false;

    @JsonProperty("description")
    private String description;

    @JsonProperty("inputs")
    private List<Map<String, Object>> inputs = new ArrayList<>();

    @JsonProperty("outputs")
    private List<Map<String, Object>> outputs = new ArrayList<>();

    @JsonProperty("decorators")
    private List<String> decorators = new ArrayList<>();

    @JsonProperty("bases")
    private List<String> bases = new ArrayList<>();

    @JsonProperty("entry_point_kinds")
    private List<String> entryPointKinds = new ArrayList<>();

    @JsonProperty("is_abstract")
    private boolean isAbstract = false;

    @JsonProperty("resiliency")
    private List<ResiliencySignal> resiliency = new ArrayList<>();

    @JsonProperty("type_")
    private String type_ = null;  // For fields: the type of the field

    @JsonProperty("visibility")
    private String visibility = null;  // "public", "private", "protected", "package"

    @JsonProperty("static_")
    private boolean static_ = false;

    @JsonProperty("final_")
    private boolean final_ = false;

    @JsonProperty("locals")
    private List<Map<String, Object>> locals = new ArrayList<>();  // Method-level variables

    @JsonProperty("fields")
    private List<Map<String, Object>> fields = new ArrayList<>();  // Class-level fields/attributes

    public Symbol() {}

    public Symbol(String id, String kind, String name, String file, int line, String module, String parent) {
        this.id = id;
        this.kind = kind;
        this.name = name;
        this.file = file;
        this.line = line;
        this.module = module;
        this.parent = parent;
    }

    @JsonProperty("entry_point")
    public String getEntryPoint() {
        return entryPointKinds.isEmpty() ? null : entryPointKinds.get(0);
    }

    public void markEntryPoint(String kind) {
        if (!entryPointKinds.contains(kind)) {
            entryPointKinds.add(kind);
        }
    }

    public String getId() {
        return id;
    }

    public String getKind() {
        return kind;
    }

    public String getName() {
        return name;
    }

    public String getFile() {
        return file;
    }

    public int getLine() {
        return line;
    }

    public Integer getEndLine() {
        return endLine;
    }

    public void setEndLine(Integer endLine) {
        this.endLine = endLine;
    }

    public String getModule() {
        return module;
    }

    public String getParent() {
        return parent;
    }

    public boolean isAsync_() {
        return async_;
    }

    public void setAsync_(boolean async_) {
        this.async_ = async_;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public List<Map<String, Object>> getInputs() {
        return inputs;
    }

    public void addInput(String paramName, String type, String defaultValue) {
        Map<String, Object> input = new HashMap<>();
        input.put("name", paramName);
        input.put("type", type != null ? type : "unknown");
        input.put("default", defaultValue);
        inputs.add(input);
    }

    public List<Map<String, Object>> getOutputs() {
        return outputs;
    }

    public void addOutput(String returnType, String evidence) {
        Map<String, Object> output = new HashMap<>();
        output.put("type", returnType != null ? returnType : "void");
        output.put("evidence", evidence != null ? evidence : "annotation");
        outputs.add(output);
    }

    public List<String> getDecorators() {
        return decorators;
    }

    public void addDecorator(String decorator) {
        if (!decorators.contains(decorator)) {
            decorators.add(decorator);
        }
    }

    public List<String> getBases() {
        return bases;
    }

    public void addBase(String baseName) {
        if (!bases.contains(baseName)) {
            bases.add(baseName);
        }
    }

    public List<String> getEntryPointKinds() {
        return entryPointKinds;
    }

    public boolean isAbstract() {
        return isAbstract;
    }

    public void setAbstract(boolean isAbstract) {
        this.isAbstract = isAbstract;
    }

    public List<ResiliencySignal> getResiliency() {
        return resiliency;
    }

    public void addResiliencySignal(ResiliencySignal signal) {
        resiliency.add(signal);
    }

    public String getType() {
        return type_;
    }

    public void setType(String type) {
        this.type_ = type;
    }

    public String getVisibility() {
        return visibility;
    }

    public void setVisibility(String visibility) {
        this.visibility = visibility;
    }

    public boolean isStatic() {
        return static_;
    }

    public void setStatic(boolean isStatic) {
        this.static_ = isStatic;
    }

    public boolean isFinal() {
        return final_;
    }

    public void setFinal(boolean isFinal) {
        this.final_ = isFinal;
    }

    public List<Map<String, Object>> getLocals() {
        return locals;
    }

    public void setLocals(List<Map<String, Object>> locals) {
        this.locals = locals;
    }

    public List<Map<String, Object>> getFields() {
        return fields;
    }

    public void setFields(List<Map<String, Object>> fields) {
        this.fields = fields;
    }
}
