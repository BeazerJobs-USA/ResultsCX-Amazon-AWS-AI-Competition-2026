# My contributions and development notes

I'm George Beazer, a senior cybersecurity student at KSU. This was a six person academic competition project. I assisted with coding, troubleshooting, AWS integration, data handling, the Streamlit graphical interface, prompt workflow, and security minded handling of sensitive data. Other teammates also contributed to the application and presentation, and I do not claim to have written the entire project.

| Area | What I worked on | Relevant code |
| --- | --- | --- |
| Windows/VS Code setup | Configured the local GitHub project, Python virtual environment, working directories and package installation with `uv` | `requirements.txt`, setup instructions |
| AWS integration | Fixed the UUID type passed to `runtimeSessionId` and troubleshot Boto3 calls | `harness_client.py`, `main.py` |
| UI | Built and debugged how QA findings, supervisor input and training sections move through Streamlit | `app.py` |
| Data | Investigated Excel retrieval and data-processing issues | `get_data.py` |
| Prompt design | Developed the stage-specific instructions and supervisor handoff logic | `prompts.py`, `pipeline.py` |
| Security and confidentiality | Worked on separating local configuration, pseudonymizing names, masking common identifiers, gating private data reads and cloud sends, and checking the data being submitted | `.env.example`, `get_data.py`, `pipeline.py`, `.gitignore` |

Later code revisions added synthetic sample data, tighter citation checks, private data access guards, and an offline walkthrough. Those changes are separate from the work completed during the two day competition. The privacy controls reflect healthcare confidentiality and HIPAA aware design concerns, but they are not a claim that the prototype is HIPAA compliant.

## November 2026 rebuild

After the competition I rebuilt the application's structure, interface and tests **with AI coding assistance (Claude Code)**. This is later work, not something that ran at the September competition, and I'm noting the assistance so nobody reads these changes as hand-written by me alone.

What changed:

1. **Four-stage interface.** `app.py` is now a short entry point. Each workspace has its own module under `ui/`, and the logic behind them is in `workflow/` with no Streamlit import.
2. **Supervisor decisions.** Approve, reject, edit, request evidence, confirm or dismiss a control, refer a suspected privacy issue. Notes are required for rejections and changes. The AI's original text is kept beside the final decision.
3. **Compliance flags and an audit trail.** Rule-based Low, Moderate, High and Critical levels, supervisor actions on each flag, and a hash-chained log of what was done.
4. **Coaching.** The supervisor picks components first. The knowledge check is graded. The roleplay branches on the choice made. Guided practice, a remediation plan, follow-up evaluation and completion tracking were added.
5. **Simulated HR referral.** Clearly labeled, in memory only, never recommended by the AI.
6. **Uploads.** CSV or JSON files are validated, pseudonymized and masked, and treated as private.
7. **Dashboard and calculator.** Counts from the loaded data and recorded actions, and an estimate with validated inputs and visible formulas.
8. **Tests.** Workflow logic, real Streamlit `AppTest`, and a headless Chrome test. See [Verification](VERIFICATION.md).

Earlier maintenance changes from the first public version remain: the masking layer at the Bedrock boundary, stricter citation checks (record ID, behavior and quote must match), and configurable AWS timeouts. I haven't claimed that I operated or stress-tested the team's original live AWS runtime, and I haven't tested the rebuilt app against live AWS either.

## Team credit

My teammates were Kevin, Jackendy, Kylee, Jack and Nelson. I have not assigned individual code ownership to them without confirmation. I do not claim to have written the entire project myself.

## Commit conventions

I use short, specific commit messages, such as:

```text
fix: require record and quote matches for QA citations
security: keep private records out of public demo mode
test: exercise synthetic app flow without AWS
docs: explain InvokeHarness and competition limitations
```

For meaningful changes I include the reason, what I tested, and any remaining limitation. I won't backdate or invent commits to make the project look older or larger than it is.

