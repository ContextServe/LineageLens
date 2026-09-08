; JavaScript reference observations.

(call_expression
  function: (identifier) @ref.name
  arguments: (arguments (_) @ref.arg)?) @ref.call

(call_expression
  function: (member_expression
              object: (_) @receiver
              property: (property_identifier) @ref.name)
  arguments: (arguments (_) @ref.arg)?) @ref.call

(new_expression
  constructor: (identifier) @ref.name
  arguments: (arguments (_) @ref.arg)?) @ref.instantiate

(class_heritage (identifier) @ref.name) @ref.inherit

(decorator (identifier) @ref.name) @ref.decorate

(decorator
  (call_expression
    function: (identifier) @ref.name
    arguments: (arguments (_) @ref.arg)?)) @ref.decorate

(import_statement source: (string) @ref.name) @ref.import

(import_specifier name: (identifier) @ref.name) @ref.import

(namespace_import (identifier) @ref.name) @ref.import

; `require('./repo')` -- still the dominant form in CommonJS code.
(call_expression
  function: (identifier) @_req
  arguments: (arguments (string) @ref.name)
  (#eq? @_req "require")) @ref.import

(throw_statement
  (new_expression constructor: (identifier) @ref.name)) @ref.throw
