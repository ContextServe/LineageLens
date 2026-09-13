; Java intraprocedural def-use facts (§9).

; ---------------------------------------------------------------------------
; writes
; ---------------------------------------------------------------------------

; Declaration with initialiser: `int total = base + tax;`
(variable_declarator
  name: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

; Plain assignment: `total = amount;`
(assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; Field write: `this.total = amount;` -- the case that makes "where does this
; field's value come from" answerable.
(assignment_expression
  left: (field_access field: (identifier) @flow.name)
  right: (_) @flow.value) @flow.write

; Bare field write inside a method: `total = amount;` where total is a field.
; Indistinguishable from a local write by syntax; the resolver decides which,
; using the containment tree.
(assignment_expression
  left: (array_access
          array: (identifier) @flow.name)
  right: (_) @flow.value) @flow.write

; Enhanced for binds its variable.
(enhanced_for_statement
  name: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

; try-with-resources binds a resource.
(resource
  name: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

; ---------------------------------------------------------------------------
; reads
; ---------------------------------------------------------------------------

; Argument at a call site. With PARAM_BINDS this is what carries a value across
; a call boundary.
(method_invocation
  arguments: (argument_list (identifier) @flow.name)) @flow.read

(method_invocation
  arguments: (argument_list
               (field_access field: (identifier) @flow.name))) @flow.read

(object_creation_expression
  arguments: (argument_list (identifier) @flow.name)) @flow.read

; Value side of an assignment or declaration.
(assignment_expression right: (identifier) @flow.name) @flow.read
(assignment_expression
  right: (field_access field: (identifier) @flow.name)) @flow.read
(variable_declarator value: (identifier) @flow.name) @flow.read
(variable_declarator
  value: (field_access field: (identifier) @flow.name)) @flow.read

; Operands.
(binary_expression left: (identifier) @flow.name) @flow.read
(binary_expression right: (identifier) @flow.name) @flow.read

; Array base and index.
(array_access array: (identifier) @flow.name) @flow.read
(array_access index: (identifier) @flow.name) @flow.read

; Receiver of a call is a read of that reference.
(method_invocation object: (identifier) @flow.name) @flow.read

; ---------------------------------------------------------------------------
; returns
; ---------------------------------------------------------------------------
;
; Captured as a read of the returned name. The RETURNS edge is built by the
; resolver, which is the only component that knows the enclosing callable's id
; and can link a callee's return to the call site's assignment target.

(return_statement (identifier) @flow.name) @flow.read

(return_statement (field_access field: (identifier) @flow.name)) @flow.read
