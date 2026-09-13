; C# reference observations.

; ---------------------------------------------------------------------------
; calls
; ---------------------------------------------------------------------------

(invocation_expression
  function: (identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; `repo.Save(order)` / `Console.WriteLine(x)`
(invocation_expression
  function: (member_access_expression
              expression: (_) @receiver
              name: (identifier) @ref.name)
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; `base.Handle(r)` needs no pattern of its own: `base` is an anonymous token in
; this grammar, so the member_access_expression pattern above already matches
; it and captures `base` as the receiver hint.

; ---------------------------------------------------------------------------
; instantiation
; ---------------------------------------------------------------------------

(object_creation_expression
  type: (identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.instantiate

(object_creation_expression
  type: (generic_name (identifier) @ref.name)) @ref.instantiate

; ---------------------------------------------------------------------------
; inheritance
; ---------------------------------------------------------------------------
;
; C# writes both base class and interfaces in one `base_list` and does not
; distinguish them syntactically -- `class S : Base, IH` gives no hint which is
; which. Emitted as `inherit`; the resolver reclassifies to IMPLEMENTS once the
; target's kind is known (an interface target means IMPLEMENTS). Guessing from
; an `I` name prefix would be a convention, not a fact.

(base_list (identifier) @ref.name) @ref.inherit

(base_list (generic_name (identifier) @ref.name)) @ref.inherit

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(variable_declaration type: (identifier) @ref.name) @ref.type

(parameter type: (identifier) @ref.name) @ref.type

(generic_name (identifier) @ref.name) @ref.type

(type_argument_list (identifier) @ref.name) @ref.type

; ---------------------------------------------------------------------------
; attributes
; ---------------------------------------------------------------------------
;
; ASP.NET routing lives here: [HttpGet("/api/users/{id}")], [Route(...)].

(attribute
  name: (identifier) @ref.name
  (attribute_argument_list (_) @ref.arg)?) @ref.decorate

(attribute name: (qualified_name) @ref.name) @ref.decorate

; ---------------------------------------------------------------------------
; imports
; ---------------------------------------------------------------------------

(using_directive (qualified_name) @ref.name) @ref.import
(using_directive (identifier) @ref.name) @ref.import

; ---------------------------------------------------------------------------
; exceptions
; ---------------------------------------------------------------------------

(throw_statement
  (object_creation_expression type: (identifier) @ref.name)) @ref.throw
