; TSX-only reference observations.
;
; Appended to refs.scm when compiling against the TSX grammar. These node types
; do not exist in the plain TypeScript grammar, so a query naming them fails to
; compile there -- hence the overlay rather than a shared pattern.
;
; `<UserCard />` is a call to a component. Capitalised names only: a lowercase
; tag is an HTML element, not a symbol in the graph. Schema 3's fallback scanner
; matched `<([A-Z]\w+)` with a regex over raw lines, so it also matched inside
; strings and comments.

(jsx_self_closing_element
  name: (identifier) @ref.name
  (#match? @ref.name "^[A-Z]")) @ref.call

(jsx_opening_element
  name: (identifier) @ref.name
  (#match? @ref.name "^[A-Z]")) @ref.call

(jsx_self_closing_element
  name: (member_expression property: (property_identifier) @ref.name)) @ref.call

(jsx_opening_element
  name: (member_expression property: (property_identifier) @ref.name)) @ref.call
