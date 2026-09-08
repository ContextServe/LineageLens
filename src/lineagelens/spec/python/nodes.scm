; Python node declarations.
;
; Each pattern declares exactly one @node.<kind>. The engine derives containment
; from span nesting, so nothing here needs to describe class/method structure --
; a method inside a class is discovered by geometry, not by a nested pattern.
;
; Schema 3 produced four node kinds for Python (class, function, method,
; module_scope) and never populated Symbol.fields at all, so the advertised
; list_fields_by_type tool could not return anything. Fields, parameters,
; variables and constants are all first-class here.

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

; A def whose first statement is a string literal: capture it as the docstring.
; The leading `.` anchors the statement as the block's FIRST child -- a trailing
; `.` would mean *last*, and would only match a function whose body is nothing
; but its docstring.
(function_definition
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @return_type
  body: (block
          . (expression_statement (string) @docstring))) @node.function

(function_definition
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @return_type) @node.function

; `async def`. The modifier capture drives NodeFlags.ASYNC.
(function_definition
  "async" @modifier
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @return_type) @node.function

; The @node.function anchor sits on the inner function_definition, not on the
; decorated_definition wrapper. Anchoring on the wrapper would give a different
; span from the undecorated pattern, so the two would not merge and every
; decorated function would appear twice (see engine._merge_by_span).
(decorated_definition
  (decorator) @decorator
  definition: (function_definition
                name: (identifier) @name
                parameters: (parameters) @params
                return_type: (_)? @return_type) @node.function)

; ---------------------------------------------------------------------------
; parameters
; ---------------------------------------------------------------------------
;
; Parameters are nodes, not a substring of the signature. Two reasons: overload
; identity needs the type list (§6), and PARAM_BINDS needs a real target to
; bind an argument to (§9).

; The anchor is on the identifier, not on the enclosing `parameters` node:
; anchoring outside would give every plain parameter the span of the whole
; parameter list, so the typed parameters (which have their own, smaller spans)
; would nest inside it and the containment tree would make siblings into
; ancestors.
(parameters (identifier) @name @node.parameter)

(typed_parameter
  (identifier) @name
  type: (type) @type) @node.parameter

(default_parameter
  name: (identifier) @name) @node.parameter

(typed_default_parameter
  name: (identifier) @name
  type: (type) @type) @node.parameter

(list_splat_pattern (identifier) @name) @node.parameter
(dictionary_splat_pattern (identifier) @name) @node.parameter

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(class_definition
  name: (identifier) @name
  body: (block
          . (expression_statement (string) @docstring))) @node.class

(class_definition
  name: (identifier) @name
  type_parameters: (_)? @type_param) @node.class

(decorated_definition
  (decorator) @decorator
  definition: (class_definition name: (identifier) @name) @node.class)

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------
;
; Python does not distinguish a class attribute from a local by syntax -- both
; are assignments -- so containment decides. The engine nests these under
; whatever encloses them, which makes an assignment in a class body a field and
; the same syntax in a function body a variable. Kind is therefore assigned by
; the pattern's context below rather than by the statement shape alone.

; Annotated class-body or module-level assignment: `x: int = 1`
(class_definition
  body: (block
          (expression_statement
            (assignment
              left: (identifier) @name
              type: (type) @type)) @node.field))

(class_definition
  body: (block
          (expression_statement
            (assignment left: (identifier) @name)) @node.field))

; `self.x = ...` in any method body is an instance field.
(assignment
  left: (attribute
          object: (identifier) @_recv
          attribute: (identifier) @name)
  (#eq? @_recv "self")) @node.field

; Annotated local
(function_definition
  body: (block
          (expression_statement
            (assignment
              left: (identifier) @name
              type: (type) @type)) @node.variable))

(function_definition
  body: (block
          (expression_statement
            (assignment left: (identifier) @name)) @node.variable))

; Module-level UPPER_SNAKE is a constant by convention. Distinguishing it
; matters for planning queries: a constant is a configuration surface, whereas
; a module-level mutable is state.
(module
  (expression_statement
    (assignment left: (identifier) @name)) @node.constant
  (#match? @name "^[A-Z][A-Z0-9_]*$"))

(module
  (expression_statement
    (assignment left: (identifier) @name)) @node.variable
  (#not-match? @name "^[A-Z][A-Z0-9_]*$"))
