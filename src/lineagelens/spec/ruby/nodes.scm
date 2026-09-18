; Ruby, level L0 (inventory).
;
; Node names taken from tree-sitter-ruby 0.23.1 by parsing a sample.
;
; L0 only. A Ruby call graph is not expressible as a query: almost every call
; is dynamically dispatched, and `method_missing`, `define_method` and
; `send` mean the set of methods on a class is not statically knowable. A
; refs.scm here would produce confident wrong edges, which is worse than
; producing none -- so this language stays at L0 until a Tier B resolver
; exists for it.

(module name: (constant) @name) @node.module

(class name: (constant) @name) @node.class

; `class << self` and anonymous singleton classes have no name to record.
(singleton_class value: (self)) @node.class

(method name: (identifier) @name
        parameters: (method_parameters)? @params) @node.method

(singleton_method name: (identifier) @name
                  parameters: (method_parameters)? @params) @node.method

; `CONST = 1`. A leading capital is Ruby's only constant marker, and the
; grammar already distinguishes `constant` from `identifier`, so this does not
; need a predicate.
(assignment left: (constant) @name) @node.constant

(assignment left: (instance_variable) @name) @node.field
