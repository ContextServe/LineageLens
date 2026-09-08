; C# intraprocedural def-use facts (§9).

; ---------------------------------------------------------------------------
; writes
; ---------------------------------------------------------------------------

; The initialiser is a positional child -- this grammar exposes no `value:`
; field and no equals_value_clause wrapper. `(_)` matches named nodes only, so
; the `=` token is not a candidate.
(variable_declarator
  name: (identifier) @flow.name
  (_) @flow.value) @flow.write

(assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `this.Addr = addr` / `_repo.Count = n`
(assignment_expression
  left: (member_access_expression name: (identifier) @flow.name)
  right: (_) @flow.value) @flow.write

; `foreach (var item in items)`
(foreach_statement
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `catch (Exception ex)`
(catch_declaration name: (identifier) @flow.name) @flow.write

; `new Server { Addr = a }` binds a member at construction.
(initializer_expression
  (assignment_expression
    left: (identifier) @flow.name
    right: (identifier) @flow.value)) @flow.write

; ---------------------------------------------------------------------------
; reads
; ---------------------------------------------------------------------------

(invocation_expression
  arguments: (argument_list (argument (identifier) @flow.name))) @flow.read

(invocation_expression
  arguments: (argument_list
               (argument
                 (member_access_expression name: (identifier) @flow.name)))) @flow.read

(object_creation_expression
  arguments: (argument_list (argument (identifier) @flow.name))) @flow.read

(assignment_expression right: (identifier) @flow.name) @flow.read

; `var amount = this.total;` -- the initialiser of a declaration is a read.
; Without these, a C# body produced writes and no reads at all, so nothing
; flowed anywhere.
(variable_declarator (identifier) @_n (identifier) @flow.name) @flow.read

(variable_declarator
  (member_access_expression name: (identifier) @flow.name)) @flow.read

(binary_expression left: (identifier) @flow.name) @flow.read
(binary_expression right: (identifier) @flow.name) @flow.read

(element_access_expression expression: (identifier) @flow.name) @flow.read

; Receiver of a member call is a read of that reference.
(invocation_expression
  function: (member_access_expression expression: (identifier) @flow.name)) @flow.read

; `$"{value}"`
(interpolation (identifier) @flow.name) @flow.read

(await_expression (identifier) @flow.name) @flow.read

; ---------------------------------------------------------------------------
; returns
; ---------------------------------------------------------------------------

(return_statement (identifier) @flow.name) @flow.read

(return_statement
  (member_access_expression name: (identifier) @flow.name)) @flow.read
