; Bash, level L0 (inventory).
;
; Node names taken from tree-sitter-bash 0.25.1 by parsing a sample.
;
; Included early despite being a small language because shell scripts are
; where a repository's deploy, build and migration steps actually live, and
; "what exists and where" is most of what anyone wants from them.
;
; L0 only. A shell call graph would have to resolve command names against
; PATH, aliases and functions defined in sourced files -- that is a runtime
; question, not a syntactic one.

(function_definition name: (word) @name) @node.function

; `NAME=1` at file scope. Shell has no declaration keyword, so a top-level
; assignment is the closest thing to a module-level variable.
(variable_assignment name: (variable_name) @name) @node.variable

; `readonly NAME=1` / `declare -r NAME=1`. Captured as a constant because the
; declaration says so, rather than inferring intent from case.
(declaration_command
  (variable_assignment name: (variable_name) @name)) @node.constant
