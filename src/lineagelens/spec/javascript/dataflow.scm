; JavaScript intraprocedural def-use facts (§9).

(variable_declarator
  name: (identifier) @flow.name
  value: (_) @flow.value) @flow.write

(assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

(assignment_expression
  left: (member_expression property: (property_identifier) @flow.name)
  right: (_) @flow.value) @flow.write

(augmented_assignment_expression
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

(field_definition
  property: (property_identifier) @flow.name
  value: (_) @flow.value) @flow.write

(for_in_statement
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

(catch_clause parameter: (identifier) @flow.name) @flow.write

(call_expression
  arguments: (arguments (identifier) @flow.name)) @flow.read

(call_expression
  arguments: (arguments
               (member_expression property: (property_identifier) @flow.name))) @flow.read

(new_expression arguments: (arguments (identifier) @flow.name)) @flow.read

(variable_declarator value: (identifier) @flow.name) @flow.read

(assignment_expression right: (identifier) @flow.name) @flow.read

(binary_expression left: (identifier) @flow.name) @flow.read
(binary_expression right: (identifier) @flow.name) @flow.read

(subscript_expression object: (identifier) @flow.name) @flow.read
(subscript_expression index: (identifier) @flow.name) @flow.read

(call_expression
  function: (member_expression object: (identifier) @flow.name)) @flow.read

(template_substitution (identifier) @flow.name) @flow.read

(await_expression (identifier) @flow.name) @flow.read

(return_statement (identifier) @flow.name) @flow.read

(return_statement
  (member_expression property: (property_identifier) @flow.name)) @flow.read
