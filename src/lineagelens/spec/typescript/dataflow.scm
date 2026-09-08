; TypeScript / TSX intraprocedural def-use facts (§9).

; ---------------------------------------------------------------------------
; writes
; ---------------------------------------------------------------------------

(variable_declarator
  name: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

(assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `this.total = amount`
(assignment_expression
  left: (member_expression property: (property_identifier) @flow.name)
  right: (_) @flow.value) @flow.write

(augmented_assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; Class field initialiser.
(public_field_definition
  name: (property_identifier) @flow.name
  value: (_) @flow.value) @flow.write

; `for (const item of items)`
(for_in_statement
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `catch (err)`
(catch_clause parameter: (identifier) @flow.name) @flow.write

; ---------------------------------------------------------------------------
; reads
; ---------------------------------------------------------------------------

(call_expression
  arguments: (arguments (identifier) @flow.name)) @flow.read

(call_expression
  arguments: (arguments
               (member_expression property: (property_identifier) @flow.name))) @flow.read

(new_expression arguments: (arguments (identifier) @flow.name)) @flow.read

(variable_declarator value: (identifier) @flow.name) @flow.read
(variable_declarator
  value: (member_expression property: (property_identifier) @flow.name)) @flow.read

(assignment_expression right: (identifier) @flow.name) @flow.read

(binary_expression left: (identifier) @flow.name) @flow.read
(binary_expression right: (identifier) @flow.name) @flow.read

(subscript_expression object: (identifier) @flow.name) @flow.read
(subscript_expression index: (identifier) @flow.name) @flow.read

; Receiver of a member call is a read of that reference.
(call_expression
  function: (member_expression object: (identifier) @flow.name)) @flow.read

; `${value}` in a template literal.
(template_substitution (identifier) @flow.name) @flow.read

; `await promise`
(await_expression (identifier) @flow.name) @flow.read

; ---------------------------------------------------------------------------
; returns
; ---------------------------------------------------------------------------

(return_statement (identifier) @flow.name) @flow.read

(return_statement
  (member_expression property: (property_identifier) @flow.name)) @flow.read
