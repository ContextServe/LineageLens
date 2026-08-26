package com.lineagelens.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.ArrayList;
import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class Container {
    @JsonProperty("id")
    private String id;

    @JsonProperty("kind")
    private String kind; // "package", "module"

    @JsonProperty("name")
    private String name;

    @JsonProperty("file")
    private String file;

    @JsonProperty("parent")
    private String parent;

    @JsonProperty("children")
    private List<String> children = new ArrayList<>();

    @JsonProperty("docstring")
    private String docstring;

    public Container() {}

    public Container(String id, String kind, String name, String file, String parent) {
        this.id = id;
        this.kind = kind;
        this.name = name;
        this.file = file;
        this.parent = parent;
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

    public String getParent() {
        return parent;
    }

    public List<String> getChildren() {
        return children;
    }

    public void addChild(String childId) {
        if (!children.contains(childId)) {
            children.add(childId);
        }
    }

    public String getDocstring() {
        return docstring;
    }

    public void setDocstring(String docstring) {
        this.docstring = docstring;
    }
}
