# Security and data handling notes

The healthcare member-service use case makes confidentiality a design requirement. The aim: use synthetic data by default, mask identifiers before anything reaches a model, require separate switches before private data is read, sent to AWS or saved, and keep credentials and company files out of the repository.

These are **HIPAA-aware confidentiality controls, not a claim of HIPAA compliance.** Nothing here has had a privacy or legal review, and the masking is regular-expression based.

## Controls in the code

| Area | What is there | Where |
| --- | --- | --- |
| Public data | `sample_data/synthetic_qa.json` holds 48 invented records and 4 invented employees. | `sample_data/` |
| Data source | `QA_DATA_SOURCE` defaults to `synthetic`. Reading local workbooks needs `QA_DATA_SOURCE=private` and `ALLOW_PRIVATE_QA_READ=1`. | `get_data.py` |
| Uploads | Limit of 2 MiB and 5,000 rows. Unknown columns are ignored and reported. **One bad row rejects the whole file**, so a partly loaded dataset cannot pass for a complete one. Employee and team-lead names become codes (`Agent-01`, `TL-01`) and identifiers are masked before anything is shown. | `workflow/intake.py` |
| Uploads are private | An uploaded file or local export is never treated as synthetic. It cannot be sent to AWS without `ALLOW_PRIVATE_CLOUD_SEND=1` or saved without `ALLOW_PRIVATE_OUTPUT_STORAGE=1`. | `pipeline.py` |
| Masking before any model call | Emails, US-style phone numbers, SSN patterns, dates, labeled member/patient/account/claim identifiers, labeled person names, long numbers, and known staff names. Applied recursively to the data payload and again to the final prompt (supervisor notes can be added after intake). The tool callback uses the same masked payload. | `security_controls.py`, `pipeline.py` |
| Untrusted text on screen | Evaluator comments, uploaded values, typed notes, and model output are escaped before they reach `st.markdown`, so they cannot add links, images or HTML. No page uses `unsafe_allow_html`. | `security_controls.escape_markdown`, `ui/components.py` |
| Evidence integrity | A citation counts as verified only if the record ID exists, belongs to the diagnosed behavior, and the quote matches the stored comment. Every metric is recomputed in Python and never taken from the model. | `pipeline.py` |
| No invented evidence | The app has no audio. It does not create transcripts, timestamps, quotes or policy citations. Missing evidence fields are shown as missing. | `workflow/intake.py`, `ui/intake.py` |
| Audit trail | Each decision and action is appended to a hash chain. Editing or removing an entry is detected on verification. | `workflow/audit.py` |
| Referrals | Held in memory only. Never written to a saved review. The audit entry for a referral leaves out the employee and the proposed action. | `workflow/operations.py` |
| Errors | `showErrorDetails = "none"` in `.streamlit/config.toml` keeps exception text out of the browser. Expected failures are explained by the app itself. | `.streamlit/config.toml`, `harness_client.describe_failure` |
| Credentials | `HARNESS_ARN` comes from the environment; Boto3's provider chain supplies credentials. `.gitignore` excludes `.env`, key files, data folders and `outputs/`. | `harness_client.py`, `.gitignore` |

## Limits and risks

1. **Masking is best effort.** Regexes miss unusual names, addresses, identifiers and free-text details. The count of masked items is not a guarantee. Do not enter real protected health information.
2. **Roles are simulated.** The sidebar's name and role fields control which buttons are enabled. They do not authenticate anyone, and anyone using the page can change them.
3. **The audit chain is tamper-evident, not tamper-proof.** It lives in session memory and, for live runs, in a plain JSON file. Someone who rewrites the whole chain defeats it.
4. **Saved files are plaintext.** Replay files are not encrypted. Private data is not saved unless `ALLOW_PRIVATE_OUTPUT_STORAGE=1`.
5. **Risk levels are rules, not findings.** Low, Moderate, High and Critical come from the issue category, repeat misses, and a keyword check on the comment. They are not legal determinations, and a missed verification step is a QA finding, not a confirmed HIPAA violation.
6. **Model output can be wrong** even when its JSON parses and its citations verify. A supervisor's decision is the control.
7. **IAM:** AWS documents that an `InvokeHarness` caller may need both `bedrock-agentcore:InvokeHarness` and `bedrock-agentcore:InvokeAgentRuntime`. I can't check any deployed policy from this repository.
8. **Streamlit's own limits:** the app has no login, so anyone who can reach the URL can use it. Run it locally or behind an authenticated proxy.

## Demonstration and production

The offline demonstration is labeled in the header and on every page that shows its content, and it never calls AWS. Switching sources asks for confirmation and clears the current run. Live mode refuses to start without a valid harness configuration and a data source that is allowed to leave the machine.

## Before a private-data run

- Get written confirmation that the specific data and cloud processing are permitted.
- Read the masked payload for names, IDs, member details and proprietary wording.
- Use temporary credentials or an approved profile; never put keys in code or Git.
- Check IAM access to the harness and its runtime.
- Leave `ALLOW_PRIVATE_OUTPUT_STORAGE` unset unless saving is allowed.
- Recheck any exported file before sharing it.

Reference: [AWS AgentCore harness permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html) and [Boto3 credential providers](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).
