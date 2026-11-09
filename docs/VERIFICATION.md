# Functional verification and limitations

This page distinguishes what the current application can demonstrate offline from what still requires an authorized AWS environment. The tests use invented data and fake network responses; they do not prove the original competition deployment was available or reliable.

## Reproducible checks

Run in Python 3.12 after installing `requirements.lock`:

```powershell
python -m unittest discover -s tests -v
python main.py --dry-run
python -m streamlit run app.py
```

The local suite has **50 passing tests**. Tests exercise actual Streamlit script state changes with `streamlit.testing.v1.AppTest`, together with plain-Python unit tests and fake Bedrock stream events.

| Feature | Checked locally | Still needed |
| --- | --- | --- |
| Synthetic QA intake, computed defect rates, evidence tables | Yes; loaded without AWS | Separate validation against any new data schema |
| Editable supervisor hours/cost calculator | Yes; changing volume and time inputs changes the output | Real preparation-time measurements before claiming business savings |
| Second privacy checkpoint, tool payload masking | Yes; synthetic identifier patterns and tool boundary tested | Human privacy review; no certified PHI de-identification |
| Immediate Compliance review flags | Yes; all failed `CO` rows are flagged, with identity/privacy priority | Organizational escalation workflow and review of real cases |
| Synthetic diagnosis, supervisor edits, approval and exclusions | Yes; app progression and handoff tested | Human assessment of the recommendation's accuracy |
| Synthetic coaching modules, quiz, scenario, scripted roleplay | Yes; module structure and alignment tests | Actual employee practice and learning-outcome validation |
| Markdown and JSON export content | Checked in the app-flow test harness | Browser download behavior on a user's machine |
| Saved replay | Yes; simulated saved-run tests | A completed authorized live run to create the saved artifacts |
| Live Bedrock invocation and generated content | SDK request shape, tool rounds, malformed streams and timeouts tested with fakes | **Real AWS credentials, permitted harness, and end-to-end live test** |

## Reliability improvements

The client now stops after a limited number of model tool rounds, reports missing AWS response streams and stream read timeouts as understandable errors, and accepts a configurable `HARNESS_READ_TIMEOUT` (120 seconds by default). A preamble from an earlier tool round no longer gets combined with the final JSON reply. The diagnosis validator reports malformed findings as errors; module validation normalizes unexpected speaker types. The citation validator checks a model quote against the sanitized evidence it was actually given.

The timeout is **idle-read**, not a deadline for the full workflow. Training may require several sequential model calls. Failed modules are shown as errors, and the Streamlit status is not marked green when a module fails. These safeguards improve failure handling but cannot guarantee the live harness will never stall.

## Current product boundaries

- The public UI is a **four-stage workflow**, not independently deployed cooperating AI agents.
- QA records and written evaluator comments are inputs; the app does not ingest or transcribe phone audio.
- The knowledge check displays a question and suggested answer but does not grade a trainee's submission.
- The roleplay is a branching **script**, not a real-time conversational simulator.
- The compliance alert is on-screen only. It does not send email, file tickets, authenticate reviewers, or determine HIPAA violations.
- The calculator estimates possible labor capacity, not measured payroll savings or reduced errors.
- The code has not been tested against the team's live AWS deployment in this verification run.

The original healthcare-partner exports, names, and other private inputs are deliberately excluded from this repository. Private-source compatibility can be checked locally in an authorized environment without sending those records to AWS or GitHub.
