; Java node declarations.
;
; This is the language schema 3 handled worst. On Apache Dubbo it produced 4,302
; nodes of which 4,297 were "classes" matched from `"class " in line`, and the
; five "methods" were string literals lifted out of a switch statement. Real
; grammar-based extraction here is the single largest coverage change: CodeGraph
; finds 30,949 methods and 7,473 fields on the same repository.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(class_declaration
  (modifiers (_) @modifier)?
  name: (identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.class

(interface_declaration
  (modifiers (_) @modifier)?
  name: (identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.interface

(enum_declaration
  (modifiers (_) @modifier)?
  name: (identifier) @name) @node.enum

(annotation_type_declaration
  (modifiers (_) @modifier)?
  name: (identifier) @name) @node.annotation

(record_declaration
  (modifiers (_) @modifier)?
  name: (identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.struct

(enum_constant name: (identifier) @name) @node.enum_member

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

(method_declaration
  (modifiers (_) @modifier)?
  type: (_) @return_type
  name: (identifier) @name
  parameters: (formal_parameters) @params
  (throws (type_identifier) @_throws)?) @node.method

(constructor_declaration
  (modifiers (_) @modifier)?
  name: (identifier) @name
  parameters: (formal_parameters) @params) @node.constructor

; Interface and abstract methods have no body; the pattern above still matches
; because `body` is not required, but an annotation-type element is a distinct
; node type and needs its own.
(annotation_type_element_declaration
  type: (_) @return_type
  name: (identifier) @name) @node.method

; ---------------------------------------------------------------------------
; parameters
; ---------------------------------------------------------------------------
;
; Nodes rather than substrings, because Java overload identity needs the type
; list (§6) and PARAM_BINDS needs a real target (§9).

(formal_parameter
  type: (_) @type
  name: (identifier) @name) @node.parameter

(spread_parameter
  (_) @type
  (variable_declarator name: (identifier) @name)) @node.parameter

; A catch parameter has no `type:` field: multi-catch (`catch (A | B e)`) means
; the type is a `catch_type` union node, captured whole rather than split.
(catch_formal_parameter
  (catch_type) @type
  name: (identifier) @name) @node.parameter

; Record components are constructor parameters and fields at once; recorded as
; parameters so the canonical constructor's signature hash is correct.
(record_declaration
  parameters: (formal_parameters
                (formal_parameter
                  type: (_) @type
                  name: (identifier) @name) @node.parameter))

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------

(field_declaration
  (modifiers (_) @modifier)?
  type: (_) @type
  declarator: (variable_declarator name: (identifier) @name)) @node.field

; `static final` is a constant, not mutable state. Worth its own kind for
; planning queries: a constant is a configuration surface, and Dubbo in
; particular keys most of its SPI wiring off `static final String` names.
; Ranked above `field` in the engine, so the two patterns matching one
; declaration merge to the more specific claim.
(field_declaration
  (modifiers
    "static"
    "final") @modifier
  type: (_) @type
  declarator: (variable_declarator name: (identifier) @name)) @node.constant

(local_variable_declaration
  type: (_) @type
  declarator: (variable_declarator name: (identifier) @name)) @node.variable
