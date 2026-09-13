; Go node declarations.
;
; Go has no classes: behaviour hangs off methods with receivers, and interface
; satisfaction is structural rather than declared. That second point is why
; IMPLEMENTS for Go is a resolver problem (whole-program method-set matching)
; rather than something a query can express -- see §15. Nothing here claims an
; IMPLEMENTS edge; the resolver derives them.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (struct_type))) @node.struct

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (interface_type))) @node.interface

; `type UserID string` -- a named type over an existing one.
(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (type_identifier) @type)) @node.type_alias

(type_alias name: (type_identifier) @name) @node.type_alias

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

(function_declaration
  name: (identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @return_type) @node.function

(method_declaration
  receiver: (parameter_list) @_receiver
  name: (field_identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @return_type) @node.method

; Interface method elements have no body but are real declarations.
(method_elem
  name: (field_identifier) @name
  parameters: (parameter_list) @params
  result: (_)? @return_type) @node.method

; ---------------------------------------------------------------------------
; parameters
; ---------------------------------------------------------------------------

(parameter_declaration
  name: (identifier) @name
  type: (_) @type) @node.parameter

(variadic_parameter_declaration
  name: (identifier) @name
  type: (_) @type) @node.parameter

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------

(field_declaration
  name: (field_identifier) @name
  type: (_) @type) @node.field

(const_declaration
  (const_spec
    name: (identifier) @name
    type: (_)? @type)) @node.constant

(var_declaration
  (var_spec
    name: (identifier) @name
    type: (_)? @type)) @node.variable

(short_var_declaration left: (expression_list (identifier) @name)) @node.variable
