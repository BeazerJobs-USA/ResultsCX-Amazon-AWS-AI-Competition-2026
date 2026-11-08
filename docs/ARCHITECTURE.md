# Architecture and Evidence Boundaries

## Purpose

The competition concept connects a QA observation to a supervisor-reviewed diagnosis, then to focused training content. The architecture below is reconstructed from participant descriptions and the user-provided dashboard visuals. The original application source has not yet been published in this repository.

## Intended processing stages

| Stage | Input | Output | Review / failure boundary |
| --- | --- | --- | --- |
| 1. Ingest | Sanitized QA worksheet or case | Normalized evidence fields | Reject missing or unexpected fields; no confidential data in the public repo |
| 2. Diagnose | QA evidence and prompt instructions | Evidence-linked issue, uncertainty and proposed cause | Model output is a hypothesis until checked |
| 3. Supervisor validation | Proposed diagnosis | Confirm, amend, or reject | Human approval is required by the intended workflow |
| 4. Coach | Confirmed gap | Focus, learning objectives, coaching conversation | Require correspondence to confirmed issue |
| 5. Practice | Approved coaching goal | Quiz, guided practice, scenario, roleplay | Fictional scripts must be labeled as practice, not company policy |
| 6. Evaluate | Training artifacts and baseline | Alignment checks and pending follow-up | Estimated labor impact is not observed savings |

The team described this as a **multi-agent supervisor pipeline**. That wording is an architectural pattern for staged tasks; this release does not establish that six separately deployed Bedrock agents communicated independently.

## Component responsibility map

| Described original module | Intended responsibility | Publicly verified in this repo? |
| --- | --- | --- |
| `app.py` | Streamlit UI and session state | No; screenshots only |
| `main.py` | Main run flow and Boto3 session ID correction | No; participant-reported fix |
| `get_data.py` | Workbook parsing and QA data retrieval | No; participant-reported fix |
| `prompts.py` | Model task instructions | No; design description only |
| `pipeline.py` | Stage orchestration and supervisor checkpoint | No; design description only |
| `harness_client.py` | Centralized AWS runtime invocation | No; suggested module boundary |
| `labor_cost.py` | Explicit assumption-driven scenario calculations | No; illustrative figures visible |

The [reference Bedrock client](../examples/bedrock_agentcore_reference.py) is an independently authored demonstration of the documented SDK contract. It does not purport to be the original code.

## AWS boundary

For an authorized AgentCore Runtime deployment:

1. The operating system supplies the ARN/region and Boto3 discovers AWS credentials through its provider chain.
2. The app constructs a JSON payload from **sanitized** input.
3. Boto3 calls `bedrock-agentcore.invoke_agent_runtime` with `agentRuntimeArn`, `runtimeSessionId` as a **string**, `contentType`, and bytes `payload`.
4. The application checks the response and handles errors without dumping raw input or secrets into logs.
5. The UI renders analysis with the human-review boundary intact.

Do not send live ResultsCX datasets to a runtime without written authorization, an approved account, necessary privacy review, and appropriate access controls.

## Evidence-versus-claim matrix

| Claim | Available evidence | Appropriate wording |
| --- | --- | --- |
| Streamlit interface was designed and demonstrated | Two user-supplied dashboard images | “Dashboard demonstration visuals show…” |
| Saved-run recovery exists in the demonstrated UI | Saved-run control shown in images | “The pictured demo includes a saved-run mode” |
| AWS invocation occurred during a specific screenshot | Not demonstrated by the image | “The team reported AWS integration; invocation not independently verified here” |
| UUID casting issue was resolved | Contributor technical account | “George reports fixing the `runtimeSessionId` type mismatch” |
| A supervisor approved a real case | Images use synthetic data and demo confirmation | “Demo indicates a supervisor confirmation checkpoint” |
| Training lowered defects | Screenshots say follow-up pending | **Do not claim improvement** |
| System was HIPAA compliant or encrypted | No control implementation evidence in this public release | **Do not claim compliance or encryption** |
| Team won or achieved a numerical score | No score sheet supplied | **Do not claim award, placement or scores** |

## Evidence still needed for stronger portfolio claims

- Sanitized original source files and traceable commits, with team consent and intellectual-property review.
- Reproducible install with locked dependency versions and a synthetic dataset.
- Minimal unit tests for input validation, transformations, and type-safe runtime session IDs.
- A real video or screen recording of the application running locally, labeled with actual recording date.
- AWS invocation proof with sensitive account IDs, ARNs, request/response bodies, and credentials redacted.
- Before/after QA data if outcomes are ever measured, including sample size and comparison period.

