; C, level L0 (inventory).
;
; Node names taken from tree-sitter-c 0.24.2 by parsing a sample, not from
; memory: a wrong node type matches nothing and the level cross-check then
; fails the conformance run, which is the intended outcome but a slow way to
; find a typo.
;
; No refs.scm or dataflow.scm, so this language is L0. That is deliberate and
; reported everywhere rather than implied: a C call graph needs preprocessor
; handling that a query cannot express.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

; `struct Point { ... };`
(struct_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @node.struct

(union_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @node.struct

(enum_specifier
  name: (type_identifier) @name
  body: (enumerator_list)) @node.enum

(enumerator name: (identifier) @name) @node.enum_member

; `typedef int Meters;`
(type_definition
  declarator: (type_identifier) @name) @node.type_alias

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

; The name sits on the inner function_declarator, not on the definition, so
; the anchor stays on function_definition while @name reaches through it --
; anchoring on the declarator would give a span that excludes the body.
(function_definition
  type: (_)? @return_type
  declarator: (function_declarator
    declarator: (identifier) @name
    parameters: (parameter_list) @params)) @node.function

; A prototype is a declaration, not a definition. Captured because a header's
; inventory is most of what a C project exposes.
(declaration
  declarator: (function_declarator
    declarator: (identifier) @name
    parameters: (parameter_list) @params)) @node.function

; ---------------------------------------------------------------------------
; members
; ---------------------------------------------------------------------------

(field_declaration
  type: (_) @type
  declarator: (field_identifier) @name) @node.field

(parameter_declaration
  type: (_) @type
  declarator: (identifier) @name) @node.parameter
