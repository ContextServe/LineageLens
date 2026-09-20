; Kotlin, level L0 (inventory).
;
; Node names and *field* names taken from tree-sitter-kotlin 1.1.0 by parsing
; a sample. Worth noting for anyone extending this: only `class_declaration`
; and `function_declaration` expose a `name:` field. `property_declaration`,
; `variable_declaration` and `class_parameter` use positional children, so
; they are matched structurally rather than by field. A field-based guess
; compiles to "Impossible pattern" and the engine refuses it, which is the
; right failure but a slow way to learn the grammar.
;
; L0 only. Kotlin is the strongest candidate for promotion, because
; `scip-java` indexes it -- so #47's SCIP oracle already gives it
; compiler-grade resolution, and a refs.scm would be resolving references a
; Tier B oracle can answer exactly. Promotion should follow measured SCIP
; coverage rather than precede it.

(class_declaration name: (identifier) @name) @node.class

(function_declaration name: (identifier) @name) @node.function

; `val p: Int = 1`
(property_declaration
  (variable_declaration (identifier) @name)) @node.property

; A primary-constructor parameter is both a parameter and, with `val`, a
; property. Recorded as a parameter: that is what the grammar says it is, and
; inferring the second role would be a claim the query cannot support.
(class_parameter (identifier) @name) @node.parameter
