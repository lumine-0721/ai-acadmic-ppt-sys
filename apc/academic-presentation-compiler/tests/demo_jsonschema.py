"""Small, non-networked API probe after upstream source review."""
import copy
from importlib.metadata import version
from jsonschema import Draft202012Validator

schema = {
    "type": "object",
    "required": ["revision", "summary"],
    "properties": {
        "revision": {"type": "integer", "minimum": 1},
        "summary": {"type": "string", "pattern": r"\S"},
    },
    "additionalProperties": True,
}
Draft202012Validator.check_schema(schema)
v = Draft202012Validator(schema)
valid = {"revision": 1, "summary": "Question", "future_field": {"x": 1}}
before = copy.deepcopy(valid)
assert not list(v.iter_errors(valid))
assert valid == before
invalid = {"revision": "1", "summary": " "}
errors = sorted(v.iter_errors(invalid), key=lambda e: list(e.absolute_path))
assert [(list(e.absolute_path), e.validator) for e in errors] == [
    (["revision"], "type"), (["summary"], "pattern")]
print(f"jsonschema={version('jsonschema')}")
print("positive=PASS extensions=preserved input=unchanged")
for error in errors:
    print(f"negative=/{'/'.join(error.absolute_path)} keyword={error.validator} message={error.message}")
print(f"schema_integer_1.0={v.is_valid({'revision': 1.0, 'summary': 'Q'})}")
print("PASS demo errors=2")
