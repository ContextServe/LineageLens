; Rust reference observations.

(call_expression
  function: (identifier) @ref.name
  arguments: (arguments (_) @ref.arg)?) @ref.call

; `self.log(x)` / `writer.write(b)`
(call_expression
  function: (field_expression
              value: (_) @receiver
              field: (field_identifier) @ref.name)
  arguments: (arguments (_) @ref.arg)?) @ref.call

; `Server::new(addr)` -- associated function.
(call_expression
  function: (scoped_identifier
              path: (_) @receiver
              name: (identifier) @ref.name)
  arguments: (arguments (_) @ref.arg)?) @ref.call

; `x.map(...)` on a generic receiver.
(macro_invocation macro: (identifier) @ref.name) @ref.call

(struct_expression name: (type_identifier) @ref.name) @ref.instantiate
(struct_expression
  name: (scoped_type_identifier name: (type_identifier) @ref.name)) @ref.instantiate

; `impl Handler for Server` -- a declared trait implementation. Distinguished
; from an inherent `impl Server` by the presence of the trait field.
(impl_item
  trait: (type_identifier) @ref.name
  type: (_) @receiver) @ref.implement

(impl_item
  trait: (generic_type type: (type_identifier) @ref.name)
  type: (_) @receiver) @ref.implement

; Supertrait bound: `trait A: B`
(trait_item
  bounds: (trait_bounds (type_identifier) @ref.name)) @ref.inherit

(type_identifier) @ref.name @ref.type

; The Rust grammar labels a generic's base `type:` rather than `name:`.
(generic_type type: (type_identifier) @ref.name) @ref.type

(scoped_type_identifier name: (type_identifier) @ref.name) @ref.type

(use_declaration argument: (scoped_identifier) @ref.name) @ref.import
(use_declaration argument: (identifier) @ref.name) @ref.import
(use_declaration argument: (use_wildcard (scoped_identifier) @ref.name)) @ref.import

; `panic!` / `unwrap` are the throw-shaped operations.
(macro_invocation
  macro: (identifier) @ref.name
  (#any-of? @ref.name "panic" "unreachable" "todo" "unimplemented")) @ref.throw
