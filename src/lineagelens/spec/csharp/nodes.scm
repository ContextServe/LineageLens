; C# node declarations.
;
; Two C# features make full fidelity a Tier B problem (§15): partial classes
; span files, so a single logical type has several declarations that must
; converge on one node id; and extension methods dispatch on static imports
; rather than on the receiver's declared type. Neither is expressible as a
; query -- the resolver handles them.
;
; Note the field name `returns:` rather than `type:` for a method's return
; type; the C# grammar differs from Java's here.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(class_declaration
  (modifier)* @modifier
  name: (identifier) @name
  type_parameters: (type_parameter_list)? @type_param) @node.class

(interface_declaration
  (modifier)* @modifier
  name: (identifier) @name
  type_parameters: (type_parameter_list)? @type_param) @node.interface

(struct_declaration
  (modifier)* @modifier
  name: (identifier) @name) @node.struct

(record_declaration
  (modifier)* @modifier
  name: (identifier) @name) @node.struct

(enum_declaration
  (modifier)* @modifier
  name: (identifier) @name) @node.enum

(enum_member_declaration name: (identifier) @name) @node.enum_member

(delegate_declaration
  (modifier)* @modifier
  name: (identifier) @name) @node.type_alias

(namespace_declaration name: (_) @name) @node.package

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

(method_declaration
  (modifier)* @modifier
  returns: (_) @return_type
  name: (identifier) @name
  parameters: (parameter_list) @params) @node.method

(constructor_declaration
  (modifier)* @modifier
  name: (identifier) @name
  parameters: (parameter_list) @params) @node.constructor

(local_function_statement
  name: (identifier) @name
  parameters: (parameter_list) @params) @node.function

; Unlike method_declaration (which uses `returns:`), an operator's return type
; is labelled `type:`.
(operator_declaration
  (modifier)* @modifier
  type: (_) @return_type
  parameters: (parameter_list) @params) @node.method

; ---------------------------------------------------------------------------
; parameters
; ---------------------------------------------------------------------------

(parameter
  type: (_) @type
  name: (identifier) @name) @node.parameter

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------
;
; `field_declaration` has no name field: it wraps a `variable_declaration`
; carrying the type, whose `variable_declarator` children carry the names. A
; single declaration may declare several fields.

(field_declaration
  (modifier)* @modifier
  (variable_declaration
    type: (_) @type
    (variable_declarator name: (identifier) @name))) @node.field

(property_declaration
  (modifier)* @modifier
  type: (_) @type
  name: (identifier) @name) @node.property

(event_field_declaration
  (variable_declaration
    type: (_) @type
    (variable_declarator name: (identifier) @name))) @node.field

; `const int Max = 3;` -- the modifier capture drives NodeFlags.FINAL, and the
; distinct node kind marks it as a configuration surface rather than state.
(field_declaration
  (modifier) @modifier
  (variable_declaration
    type: (_) @type
    (variable_declarator name: (identifier) @name))
  (#eq? @modifier "const")) @node.constant

(local_declaration_statement
  (variable_declaration
    type: (_) @type
    (variable_declarator name: (identifier) @name))) @node.variable
