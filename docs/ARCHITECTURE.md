# Architecture notes

I kept the processing stages separate because the QA numbers, model interpretation, supervisor judgment, and training output should not be treated as the same kind of evidence.

## What the code does

| Module | What it does |
| --- | --- |
| `get_data.py` | Loads nine invented QA records by default. With explicit authorization switches, it can read local Excel exports and apply limited masking. It computes the rates in Python. |
| `prompts.py` | Renders the metrics/evidence into a JSON-constrained diagnosis prompt and a separate prompt for each selected coaching module. |
| `harness_client.py` | Builds `InvokeHarness` requests, handles streamed text/tool events, and gives common failures actionable messages. It reuses one string session ID for related requests. |
| `pipeline.py` | Validates JSON, matches record IDs/behaviors/quotes, applies supervisor edits, requests training, performs structural alignment checks, and optionally saves results. |
| `app.py` | Presents QA data, diagnosis, review and coaching in Streamlit. Supports live, saved-run and synthetic walkthrough modes. |
| `sample_walkthrough.py` | Provides handwritten fictional findings and modules for an AWS-free public demonstration. |
| `main.py` | Supports offline checks and an explicitly requested live terminal run. |

## Stage boundaries

| Stage | Input and decision | Output |
| --- | --- | --- |
| **1. Data intake** | Synthetic records or authorized local QA exports; deterministic Python calculations | Behavior metrics, agent-date proxy counts, evidence rows |
| **2. AI diagnosis** | The harness processes selected evidence and structured prompts | Proposed root causes, cited records, intervention suggestions and citation checks |
| **3. Supervisor review** | A manager or supervisor can edit the cause, intervention, notes, and training selection | Reviewed findings; excluded findings do not generate modules |
| **4. Coaching generation** | A separate training prompt runs for each selected finding | Objectives, instruction, quizzes, exercises, fictional customer scenarios, scripted roleplay and alignment status |

The [main README](../README.md#the-application) illustrates the four stages. I also keep a [full-size SVG](architecture-flow.svg) and its editable [Graphviz source](architecture-flow.dot) here.

The live code uses **one AgentCore harness session** and sequential prompts for diagnosis and approved findings. There is no demonstrated network of independently deployed specialist agents. The supervisor step happens in the Streamlit UI; CLI `--full` uses a clearly labeled test-only automatic selection and should not be counted as human approval.

## Verification details

I changed citation validation because the original implementation treated an existing record ID as verified even when it belonged to a different QA behavior. It also did not verify that the model's quoted words actually appeared in the record.

Now the diagnosis check requires all three:

1. The record ID exists.
2. The record is for the same scored behavior and section.
3. The quoted phrase appears in the stored evaluator comment after whitespace/case normalization.

For training-module references, the validator checks that a cited ID exists and belongs to the right behavior. The separate alignment function uses simple keyword overlap and checks for an objective, scenario, roleplay, and scoring criteria. **That is a heuristic**, not a semantic assessment or measured learning outcome.

## What I cannot verify from this release

- Live AWS credentials, permissions, and the deployed harness. The tests validate the Boto3 request against the SDK service model with a fake client; they do not contact AWS.
- The original September demonstration's runtime state or deployment configuration. The supplied ZIP is one code snapshot, not a complete historical commit trail.
- Production privacy controls. The private-data scrubber uses regexes and known names, which can miss identifiers. The public walkthrough avoids this by using invented records.
- Training effectiveness. The screenshots show pending or simulated values, and no measured post-training data is available.

Those are the current implementation boundaries; additional runtime and outcome validation remains separate work.

