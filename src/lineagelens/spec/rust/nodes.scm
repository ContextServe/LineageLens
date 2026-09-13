; Rust node declarations.
;
; Rust's trait system is the hardest of the six for identity: blanket impls
; (`impl<T: Display> Foo for T`) have no single resolvable target, and generic
; monomorphisation means one written function corresponds to many concrete
; instantiations. IMPLEMENTS is therefore marked partial in the capability
; matrix (§12, §15) -- a declared `impl Trait for Type` is captured in refs.scm,
; while a blanket impl becomes a boundary rather than an edge.

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(struct_item
  (visibility_modifier)? @visibility
  name: (type_identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.struct

(trait_item
  (visibility_modifier)? @visibility
  name: (type_identifier) @name
  type_parameters: (type_parameters)? @type_param) @node.trait

(enum_item
  (visibility_modifier)? @visibility
  name: (type_identifier) @name) @node.enum

(enum_variant name: (identifier) @name) @node.enum_member

(type_item
  (visibility_modifier)? @visibility
  name: (type_identifier) @name) @node.type_alias

(union_item name: (type_identifier) @name) @node.struct

(mod_item name: (identifier) @name) @node.module

; ---------------------------------------------------------------------------
; callables
; ---------------------------------------------------------------------------

(function_item
  (visibility_modifier)? @visibility
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @return_type) @node.function

; A trait's required method: signature only, no body.
(function_signature_item
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @return_type) @node.function

; ---------------------------------------------------------------------------
; parameters
; ---------------------------------------------------------------------------

(parameter
  pattern: (identifier) @name
  type: (_) @type) @node.parameter

; `&self` / `&mut self` / `self`. Captured so arity is correct, then dropped
; from the signature hash via implicit_receivers in lang.toml.
(self_parameter (self) @name) @node.parameter

; ---------------------------------------------------------------------------
; values
; ---------------------------------------------------------------------------

(field_declaration
  (visibility_modifier)? @visibility
  name: (field_identifier) @name
  type: (_) @type) @node.field

(const_item
  (visibility_modifier)? @visibility
  name: (identifier) @name
  type: (_)? @type) @node.constant

(static_item
  (visibility_modifier)? @visibility
  name: (identifier) @name
  type: (_)? @type) @node.constant

(let_declaration pattern: (identifier) @name) @node.variable
