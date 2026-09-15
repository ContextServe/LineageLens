; JavaScript node declarations.
;
; Separate from the TypeScript spec rather than shared: the JS grammar has no
; type annotations, no `interface_declaration`, and uses `identifier` where TS
; uses `required_parameter`, so a shared query would fail to compile. JSX files
; (.jsx) are routed to the TSX grammar instead, which is why nothing here
; mentions JSX.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(class_declaration
  decorator: (decorator)* @decorator
  name: (identifier) @name) @node.class

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

(function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @node.function

(generator_function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @node.function

(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params) @node.method

(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params
  (#eq? @name "constructor")) @node.constructor

; `const handler = (req) => {...}`. The dominant declaration form in modern JS,
; so treating these as anonymous would lose most of a codebase.
(variable_declarator
  name: (identifier) @name
  value: (arrow_function parameters: (formal_parameters) @params)) @node.function

(variable_declarator
  name: (identifier) @name
  value: (arrow_function parameter: (identifier) @params)) @node.function

(variable_declarator
  name: (identifier) @name
  value: (function_expression parameters: (formal_parameters) @params)) @node.function

; ---------------------------------------------------------------------------
; parameters
; ---------------------------------------------------------------------------

; Anchored on the identifier rather than the enclosing `formal_parameters`, so
; each parameter gets its own span and they stay siblings.
(formal_parameters (identifier) @name @node.parameter)

(rest_pattern (identifier) @name) @node.parameter

(assignment_pattern left: (identifier) @name) @node.parameter

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------

; The JS grammar labels a class field's name `property:`, not `name:`
; (unlike TypeScript's public_field_definition, which uses `name:`).
(field_definition property: (property_identifier) @name) @node.field

; `export const MAX = 3` wraps the declaration in an export_statement, so
; requiring a direct child of `program` missed every exported constant -- which
; is most of them in a module-based codebase.
((lexical_declaration
   "const"
   (variable_declarator name: (identifier) @name)) @node.constant
 (#match? @name "^[A-Z][A-Z0-9_]*$"))

(lexical_declaration
  (variable_declarator name: (identifier) @name)) @node.variable

(variable_declaration
  (variable_declarator name: (identifier) @name)) @node.variable
