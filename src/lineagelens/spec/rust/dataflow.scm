; Rust intraprocedural def-use facts (§9).
;
; Ownership and borrowing are not modelled: tracking a move would need the
; borrow checker's analysis, so a value flowing through a `&mut` reference is a
; boundary (kind=alias) rather than an edge. What is captured is the
; deterministic part -- bindings, field writes, argument reads, returns.

(let_declaration
  pattern: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

(assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `self.addr = v`
(assignment_expression
  left: (field_expression field: (field_identifier) @flow.name)
  right: (_) @flow.value) @flow.write

(compound_assignment_expr
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `Server { addr: a }` binds a field at construction.
(field_initializer
  field: (field_identifier) @flow.name
  value: (identifier) @flow.value) @flow.write

(for_expression
  pattern: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

(call_expression arguments: (arguments (identifier) @flow.name)) @flow.read

(call_expression
  arguments: (arguments
               (field_expression field: (field_identifier) @flow.name))) @flow.read

(call_expression
  function: (field_expression value: (identifier) @flow.name)) @flow.read

(binary_expression left: (identifier) @flow.name) @flow.read
(binary_expression right: (identifier) @flow.name) @flow.read

(index_expression (identifier) @flow.name) @flow.read

(return_expression (identifier) @flow.name) @flow.read

(return_expression
  (field_expression field: (field_identifier) @flow.name)) @flow.read

; Rust's tail expression is an implicit return, and is the dominant form.
(function_item
  body: (block (identifier) @flow.name .)) @flow.read
