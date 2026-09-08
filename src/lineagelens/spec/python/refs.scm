; Python reference observations.
;
; These are observations, never edges. A pattern captures the name as written
; and, where the syntax offers one, a receiver expression to hand the resolver
; as a hint. It does not and cannot say what the name binds to -- that is the
; resolver's job (§4), and the schema-3 tree-sitter path's habit of matching on
; the bare trailing name and picking possible_targets[0] is exactly what this
; separation removes.

; ---------------------------------------------------------------------------
; calls
; ---------------------------------------------------------------------------

; Plain call: `save(order)`. Arguments are captured individually so the
; resolver can build PARAM_BINDS once the target is known (§9).
(call
  function: (identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; Attribute call: `repo.save(order)`. The receiver is the resolution hint that
; distinguishes this from `db.save` and `file.save` -- collapsing all three onto
; the name "save" is what fabricated edges in schema 3.
(call
  function: (attribute
              object: (_) @receiver
              attribute: (identifier) @ref.name)
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; ---------------------------------------------------------------------------
; types and annotations
; ---------------------------------------------------------------------------
;
; An annotation-only reference is a large class of real usage that no call edge
; describes: a request model referenced only in a route signature is live code.

(type (identifier) @ref.name) @ref.type

(type (subscript value: (identifier) @ref.name)) @ref.type

; String forward reference: `def f() -> "Order"`
(type (string) @ref.name) @ref.type

; ---------------------------------------------------------------------------
; inheritance
; ---------------------------------------------------------------------------

(class_definition
  superclasses: (argument_list (identifier) @ref.name)) @ref.inherit

(class_definition
  superclasses: (argument_list
                  (attribute attribute: (identifier) @ref.name))) @ref.inherit

; ---------------------------------------------------------------------------
; decorators
; ---------------------------------------------------------------------------
;
; `@memoize def f()` means f references memoize, so a project's own decorators
; are never dead. Direction is decorated -> decorator.

(decorator (identifier) @ref.name) @ref.decorate

; Arguments are captured because a decorator's argument is very often the
; contract key -- `@router.get("/api/users/{id}")`, `@app.task(name="x")`. A
; contract adapter (§8.2) reads it from here, and without it every
; framework-declared route is invisible.
(decorator
  (call
    function: (identifier) @ref.name
    arguments: (argument_list (_) @ref.arg)?)) @ref.decorate

(decorator
  (call
    function: (attribute
                object: (_) @receiver
                attribute: (identifier) @ref.name)
    arguments: (argument_list (_) @ref.arg)?)) @ref.decorate

(decorator (attribute attribute: (identifier) @ref.name)) @ref.decorate

; ---------------------------------------------------------------------------
; imports
; ---------------------------------------------------------------------------

(import_statement name: (dotted_name) @ref.name) @ref.import

(import_from_statement
  module_name: (dotted_name) @receiver
  name: (dotted_name) @ref.name) @ref.import

(aliased_import name: (dotted_name) @ref.name) @ref.import

; ---------------------------------------------------------------------------
; exceptions
; ---------------------------------------------------------------------------

(raise_statement (call function: (identifier) @ref.name)) @ref.throw
(raise_statement (identifier) @ref.name) @ref.throw
