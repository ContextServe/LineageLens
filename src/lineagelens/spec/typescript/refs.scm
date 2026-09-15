; TypeScript / TSX reference observations.
;
; The `fetch` / `axios` call patterns here are what the HTTP contract adapter
; reads (§8.2): the frontend side of the cross-framework join that lets a
; `fetch('/api/users/42')` reach a Python route handler through a contract node.

; ---------------------------------------------------------------------------
; calls
; ---------------------------------------------------------------------------

(call_expression
  function: (identifier) @ref.name
  arguments: (arguments (_) @ref.arg)?) @ref.call

(call_expression
  function: (member_expression
              object: (_) @receiver
              property: (property_identifier) @ref.name)
  arguments: (arguments (_) @ref.arg)?) @ref.call

; `new Foo(...)`
(new_expression
  constructor: (identifier) @ref.name
  arguments: (arguments (_) @ref.arg)?) @ref.instantiate

(new_expression
  constructor: (member_expression
                 property: (property_identifier) @ref.name)) @ref.instantiate

; ---------------------------------------------------------------------------
; inheritance
; ---------------------------------------------------------------------------

(class_heritage
  (extends_clause value: (identifier) @ref.name)) @ref.inherit

(class_heritage
  (implements_clause (type_identifier) @ref.name)) @ref.implement

(extends_type_clause (type_identifier) @ref.name) @ref.implement

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(type_annotation (type_identifier) @ref.name) @ref.type

(type_annotation
  (generic_type name: (type_identifier) @ref.name)) @ref.type

(generic_type name: (type_identifier) @ref.name) @ref.type

(type_arguments (type_identifier) @ref.name) @ref.type

; ---------------------------------------------------------------------------
; decorators
; ---------------------------------------------------------------------------
;
; NestJS and Angular wiring lives here: @Controller, @Injectable, @Get.

(decorator (identifier) @ref.name) @ref.decorate

(decorator
  (call_expression
    function: (identifier) @ref.name
    arguments: (arguments (_) @ref.arg)?)) @ref.decorate

(decorator
  (call_expression
    function: (member_expression
                object: (_) @receiver
                property: (property_identifier) @ref.name)
    arguments: (arguments (_) @ref.arg)?)) @ref.decorate

; ---------------------------------------------------------------------------
; imports and exports
; ---------------------------------------------------------------------------

(import_statement source: (string) @ref.name) @ref.import

(import_specifier name: (identifier) @ref.name) @ref.import

(namespace_import (identifier) @ref.name) @ref.import

(export_statement
  (export_clause (export_specifier name: (identifier) @ref.name))) @ref.type

; ---------------------------------------------------------------------------
; exceptions
; ---------------------------------------------------------------------------

(throw_statement
  (new_expression constructor: (identifier) @ref.name)) @ref.throw
