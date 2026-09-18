; TypeScript / TSX node declarations.
;
; Shared by both grammars. `interface` and `type_alias` matter here in a way
; they did not in schema 3, which had no node kind for either: the JS bridge
; emitted `interface` as a bare string while the Python analyser had no concept
; of one, so the two graphs could not be compared let alone merged.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(class_declaration
  decorator: (decorator)* @decorator
  name: (type_identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.class

(abstract_class_declaration
  name: (type_identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.class

(interface_declaration
  name: (type_identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.interface

(type_alias_declaration
  name: (type_identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.type_alias

(enum_declaration name: (identifier) @name) @node.enum

(enum_assignment name: (property_identifier) @name) @node.enum_member

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

(function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type) @node.function

(generator_function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @node.function

(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type) @node.method

; `constructor(...)` is a method_definition whose name is the literal
; `constructor`; separated so it gets the right node kind.
(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params
  (#eq? @name "constructor")) @node.constructor

; `async function` and `async` methods. The modifier capture drives
; NodeFlags.ASYNC (#60). Missing until then: the flag was populated only for
; Python, so every TypeScript async frame was invisible to the resiliency
; detector -- which reported "no risks" over code it could not see the
; async-ness of.
(function_declaration
  "async" @modifier
  name: (identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type) @node.function

(method_definition
  "async" @modifier
  name: (property_identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type) @node.method

; Interface and type-literal members.
(method_signature
  name: (property_identifier) @name
  parameters: (formal_parameters) @params
  return_type: (type_annotation)? @return_type) @node.method

; `const handler = (req) => {...}` and `const f = function () {}`. Arrow
; functions assigned to a name are the dominant declaration form in modern
; TS/JS, so treating them as anonymous would lose most of a codebase.
(variable_declarator
  name: (identifier) @name
  type: (type_annotation)? @return_type
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

(required_parameter
  pattern: (identifier) @name
  type: (type_annotation)? @type) @node.parameter

(optional_parameter
  pattern: (identifier) @name
  type: (type_annotation)? @type) @node.parameter

(rest_pattern (identifier) @name) @node.parameter

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------

(public_field_definition
  (accessibility_modifier)? @visibility
  name: (property_identifier) @name
  type: (type_annotation)? @type) @node.field

(property_signature
  name: (property_identifier) @name
  type: (type_annotation)? @type) @node.field

; A `const` at module scope with an UPPER_SNAKE name is a configuration
; surface; distinguishing it from mutable state is useful for planning queries.
; `export const MAX = 3` wraps the declaration in an export_statement, so
; requiring a direct child of `program` missed every exported constant -- which
; is most of them in a module-based codebase.
((lexical_declaration
   "const"
   (variable_declarator name: (identifier) @name)) @node.constant
 (#match? @name "^[A-Z][A-Z0-9_]*$"))

(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    type: (type_annotation)? @type)) @node.variable

(variable_declaration
  (variable_declarator
    name: (identifier) @name
    type: (type_annotation)? @type)) @node.variable
