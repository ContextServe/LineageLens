; Java reference observations.
;
; The receiver capture matters more here than anywhere else. Dubbo is built on
; interface dispatch, so `invoker.invoke(inv)` and `filter.invoke(inv)` are
; different targets that share the name `invoke`. Schema 3 matched on the bare
; trailing name and picked possible_targets[0], which is how it reported
; resolution=resolved on 2,593 of 2,593 edges while producing a graph with zero
; traversable paths. The receiver is the hint Tier B needs to resolve these
; correctly; without it the resolver records an ambiguity rather than guessing.

; ---------------------------------------------------------------------------
; calls
; ---------------------------------------------------------------------------

(method_invocation
  object: (_) @receiver
  name: (identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; Unqualified call: an inherited or same-class method.
(method_invocation
  name: (identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

(explicit_constructor_invocation
  constructor: (this) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

(explicit_constructor_invocation
  constructor: (super) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.call

; Method reference: `Foo::bar` is a live use of bar that no call edge describes.
(method_reference
  (_) @receiver
  (identifier) @ref.name) @ref.call

; ---------------------------------------------------------------------------
; instantiation
; ---------------------------------------------------------------------------

(object_creation_expression
  type: (type_identifier) @ref.name
  arguments: (argument_list (_) @ref.arg)?) @ref.instantiate

(object_creation_expression
  type: (generic_type (type_identifier) @ref.name)
  arguments: (argument_list (_) @ref.arg)?) @ref.instantiate

; ---------------------------------------------------------------------------
; inheritance
; ---------------------------------------------------------------------------
;
; Split into INHERITS (extends) and IMPLEMENTS, which schema 3 conflated into a
; single INHERITS kind and then emitted 2 of on an 88-class codebase.

(superclass (type_identifier) @ref.name) @ref.inherit
(superclass (generic_type (type_identifier) @ref.name)) @ref.inherit

(super_interfaces
  (type_list (type_identifier) @ref.name)) @ref.implement
(super_interfaces
  (type_list (generic_type (type_identifier) @ref.name))) @ref.implement

(extends_interfaces
  (type_list (type_identifier) @ref.name)) @ref.implement

; ---------------------------------------------------------------------------
; types
; ---------------------------------------------------------------------------

(type_identifier) @ref.name @ref.type

(generic_type (type_identifier) @ref.name) @ref.type

; ---------------------------------------------------------------------------
; annotations
; ---------------------------------------------------------------------------
;
; Dubbo and Spring are annotation-driven, so these carry the framework wiring
; the contract adapters read (§8.2): @DubboService, @Reference, @RequestMapping.

(marker_annotation name: (identifier) @ref.name) @ref.decorate

(annotation
  name: (identifier) @ref.name
  arguments: (annotation_argument_list (_) @ref.arg)?) @ref.decorate

; ---------------------------------------------------------------------------
; imports
; ---------------------------------------------------------------------------

(import_declaration (scoped_identifier) @ref.name) @ref.import

; ---------------------------------------------------------------------------
; exceptions
; ---------------------------------------------------------------------------

(throw_statement
  (object_creation_expression type: (type_identifier) @ref.name)) @ref.throw

(throws (type_identifier) @ref.name) @ref.throw
