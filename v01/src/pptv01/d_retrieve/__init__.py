"""Stage D (asset registry): real retrieval from Wikimedia Commons / Wikidata / OpenAlex / Crossref. See stage.py."""

from .stage import REGISTRY_FILE, AssetRetrievalFailed, DServices, run_stage_d, services_from_env  # noqa: F401

IMPLEMENTED = True
