; Python intraprocedural def-use facts (§9).
;
; Scope is the agreed deterministic 80%: assignment targets, value-position
; reads, field writes and return expressions. Anything needing alias analysis
; or dynamic dispatch becomes a boundary record with a candidate set, never a
; guess (§9.1).
;
; Reads and writes are emitted as references because that is what they are: the
; extractor sees a name in value position and does not know what it binds to.
; Modelling them as UnresolvedRef means one resolver handles calls and data
; alike, and an unresolvable read lands in the same accounting as an
; unresolvable call rather than vanishing.

; ---------------------------------------------------------------------------
; writes
; ---------------------------------------------------------------------------

; `x = expr`
(assignment
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `self.total = expr` -- the field-write case that makes "where does this
; field's value come from" answerable.
(assignment
  left: (attribute attribute: (identifier) @flow.name)
  right: (_) @flow.value) @flow.write

; `x += expr` is both a read and a write; the read is covered by the
; value-position patterns below.
(augmented_assignment
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

(augmented_assignment
  left: (attribute attribute: (identifier) @flow.name)
  right: (_) @flow.value) @flow.write

; Loop and comprehension binders are writes: the name is defined here.
(for_statement
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

(for_in_clause
  left: (identifier) @flow.name
  right: (_) @flow.value) @flow.write

; `with open(p) as fh`
(with_item
  value: (as_pattern
           (_) @flow.value
           alias: (as_pattern_target (identifier) @flow.name))) @flow.write

; `except E as exc`
(except_clause
  (as_pattern
    (_) @flow.value
    alias: (as_pattern_target (identifier) @flow.name))) @flow.write

; ---------------------------------------------------------------------------
; reads
; ---------------------------------------------------------------------------

; Value on the right of an assignment.
(assignment right: (identifier) @flow.name) @flow.read

; `self.x` in value position.
(assignment
  right: (attribute attribute: (identifier) @flow.name)) @flow.read

; Argument passed to a call. Combined with PARAM_BINDS this is what carries a
; value across a call boundary.
(call arguments: (argument_list (identifier) @flow.name)) @flow.read

(call
  arguments: (argument_list
               (attribute attribute: (identifier) @flow.name))) @flow.read

; Keyword argument value.
(keyword_argument value: (identifier) @flow.name) @flow.read

; Operands of a binary expression.
(binary_operator left: (identifier) @flow.name) @flow.read
(binary_operator right: (identifier) @flow.name) @flow.read

; Subscript base and index: `rows[key]` reads both.
(subscript value: (identifier) @flow.name) @flow.read
(subscript subscript: (identifier) @flow.name) @flow.read

; Interpolated value in an f-string.
(interpolation expression: (identifier) @flow.name) @flow.read

; ---------------------------------------------------------------------------
; returns
; ---------------------------------------------------------------------------
;
; Captured as a read of the returned name. The RETURNS edge itself is built by
; the resolver, which is the only component that knows the enclosing callable's
; node id and can therefore link callee return to call-site target.

(return_statement (identifier) @flow.name) @flow.read

(return_statement (attribute attribute: (identifier) @flow.name)) @flow.read

(yield (identifier) @flow.name) @flow.read
