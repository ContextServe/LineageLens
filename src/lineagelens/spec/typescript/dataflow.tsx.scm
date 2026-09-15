; TSX-only data-flow facts. See refs.tsx.scm for why this is an overlay.

; A value passed as a JSX prop crosses into the component, so it is a read of
; the outer name and -- once the component resolves -- a bind to its prop.
(jsx_expression (identifier) @flow.name) @flow.read

(jsx_attribute
  (jsx_expression (identifier) @flow.name)) @flow.read
