"""A dotted string naming a symbol. Only REFERENCES_STRING can see this, and it
is off by default -- so do_work must be rescued by its task decorator instead."""

ROUTES = {"corpus.tasks.do_work": "queue-a"}
