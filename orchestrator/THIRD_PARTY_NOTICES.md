# Third-party notices

Direct dependencies installed in academic_ppt_orchestrator/.venv:

| Package | Version executed | License | Role |
| --- | --- | --- | --- |
| langgraph | 1.2.21 | MIT | StateGraph, conditional edges, checkpoint resume |
| langgraph-checkpoint-sqlite | 3.1.3 | MIT | SQLite checkpointer |
| jsonschema | 4.25.1 | MIT | Academic Deck IR Draft 2020-12 validation |
| python-pptx | 1.0.2 | MIT | Probe PPTX only. Same pin APRD already uses |

langchain-core, langsmith, pydantic, and the other langgraph transitive packages are present only because langgraph 1.2.21 requires them. This orchestrator does not call a model provider.

Module CLIs are invoked in place. Their optional libraries are not upgraded here.

GitHub metadata consulted on 2026-11-13. Those repositories were not vendored.

| Repository | Commit from the GitHub API | License |
| --- | --- | --- |
| langchain-ai/langgraph | 3183e90eca59850a607d8a4c1b24fa8c5c8a2b69 | MIT |
| PrefectHQ/prefect | 53ad34955fbcccdff163c41ad0447d52d49be9c8 | Apache-2.0 |
| temporalio/sdk-python | 6547f47e903518ca33f232ba38d0c665d6f730ae | MIT |
| HKUDS/Paper2Slides | 0785051d1f52814097e94c44751e3f12b83f7c8a | MIT |
| icip-cas/PPTAgent | 9dacac53ac9ef39346461063df729396551d929e | MIT |
| dagster-io/dagster | 0405e5f52f52f1efe6367bb8eb807f4b531c217a | Apache-2.0 |

The code that actually ran is PyPI langgraph 1.2.21, not a checkout of the later main commit above.
