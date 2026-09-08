; Go reference observations.

(call_expression
  function: (identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; `fmt.Errorf(...)` / `s.Handle(...)`. The operand is the resolution hint that
; separates a package-qualified call from a method on a value -- collapsing both
; onto the trailing name is what fabricated edges in schema 3.
(call_expression
  function: (selector_expression
              operand: (_) @receiver
              field: (field_identifier) @ref.name)
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; `&Server{...}` / `Server{...}`
(composite_literal type: (type_identifier) @ref.name) @ref.instantiate
(composite_literal
  type: (qualified_type name: (type_identifier) @ref.name)) @ref.instantiate

; Struct embedding is Go's nearest equivalent to inheritance: an embedded
; type's methods are promoted onto the outer struct. `!name` selects the
; embedded form (a type with no field name) over a normal field.
(field_declaration
  type: (type_identifier) @ref.name
  !name) @ref.inherit

(type_identifier) @ref.name @ref.type

(qualified_type name: (type_identifier) @ref.name) @ref.type

(import_spec path: (interpreted_string_literal) @ref.name) @ref.import

; Go returns errors rather than throwing, but panic is still a throw.
(call_expression
  function: (identifier) @ref.name
  (#eq? @ref.name "panic")) @ref.throw
