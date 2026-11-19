"""Academic Deck IR public API."""
from .adapters import assemble, input_receipt, migrate
from .consumer import inspect_deck
from .model import (SCHEMA, VERSION, Conflict, Invalid, diagnostics, digest, dumps, loads,
                    new_deck, new_slide, parse, resource, validate)
from .operations import edit, merge, proposal, read_page

__all__ = ["SCHEMA", "VERSION", "Conflict", "Invalid", "assemble", "diagnostics", "digest", "dumps",
           "edit", "input_receipt", "inspect_deck", "loads", "merge", "migrate", "new_deck", "new_slide", "parse",
           "proposal", "read_page", "resource", "validate"]
