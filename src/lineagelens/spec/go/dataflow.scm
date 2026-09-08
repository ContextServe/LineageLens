; Go intraprocedural def-use facts (§9).

; ---------------------------------------------------------------------------
; writes
; ---------------------------------------------------------------------------

(short_var_declaration
  left: (expression_list (identifier) @flow.name)
  right: (expression_list (_) @flow.value)) @flow.write

(var_spec
  name: (identifier) @flow.name
  value: (expression_list (_) @flow.value)) @flow.write

(const_spec
  name: (identifier) @flow.name
  value: (expression_list (_) @flow.value)) @flow.write

(assignment_statement
  left: (expression_list (identifier) @flow.name)
  right: (expression_list (_) @flow.value)) @flow.write

; `s.addr = v` -- field write.
(assignment_statement
  left: (expression_list
          (selector_expression field: (field_identifier) @flow.name))
  right: (expression_list (_) @flow.value)) @flow.write

(range_clause
  left: (expression_list (identifier) @flow.name)
  right: (_) @flow.value) @flow.write

; `Server{Addr: addr}` binds a field at construction.
(keyed_element
  (literal_element (identifier) @flow.name)
  (literal_element (identifier) @flow.value)) @flow.write

; ---------------------------------------------------------------------------
; reads
; ---------------------------------------------------------------------------

(call_expression
  arguments: (argument_list (identifier) @flow.name)) @flow.read

(call_expression
  arguments: (argument_list
               (selector_expression field: (field_identifier) @flow.name))) @flow.read

(call_expression
  function: (selector_expression operand: (identifier) @flow.name)) @flow.read

(binary_expression left: (identifier) @flow.name) @flow.read
(binary_expression right: (identifier) @flow.name) @flow.read

(index_expression operand: (identifier) @flow.name) @flow.read
(index_expression index: (identifier) @flow.name) @flow.read

; ---------------------------------------------------------------------------
; returns
; ---------------------------------------------------------------------------

(return_statement (expression_list (identifier) @flow.name)) @flow.read

(return_statement
  (expression_list
    (selector_expression field: (field_identifier) @flow.name))) @flow.read
