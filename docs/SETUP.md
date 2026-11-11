# Setup and usage

The offline demonstration needs no AWS account. I tested the app on Python 3.13 with the pinned versions in `requirements.lock` (Streamlit 1.67, boto3 1.43). CI uses Python 3.12.

## Install and run

```bash
git clone https://github.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026.git
cd ResultsCX-Amazon-AWS-AI-Competition-2026
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.lock

python -m unittest discover -s tests -v
streamlit run app.py
```

The default source is **Offline demonstration**. In **1 · Intake & Evidence**, use "Load the offline demonstration analysis" to open the handwritten findings, then work through the stages. The browser test needs Playwright and Chrome (`pip install playwright`); it skips itself if either is missing.

## Terminal checks

```bash
python get_data.py          # print dataset totals
python main.py --dry-run    # build the prompt, no AWS call
python main.py --check      # check .env, credentials and data (no model tokens used)
```

`python main.py --live` calls Bedrock and is only for an authorized harness.

## Live mode

Only with a harness and account you are allowed to use:

1. Copy `.env.example` to `.env` and set `HARNESS_ARN` and the AWS region.
2. Sign in with a profile, IAM Identity Center, or an assumed role. Keep keys out of Git.
3. Use a current boto3 (`pip install -U boto3`). The client needs the `bedrock-agentcore` service.
4. In the app, choose **Live - Amazon Bedrock AgentCore** in the sidebar, then run the analysis from Intake.

`HARNESS_ARN` is a harness ARN: the client calls `invoke_harness`, not `invoke_agent_runtime`. Optional settings:

| Variable | Meaning |
| --- | --- |
| `HARNESS_MAX_TOKENS` | Token ceiling per call; unset uses the harness default. |
| `HARNESS_READ_TIMEOUT` | Seconds a single stream read may sit idle (15 to 600, default 120). |
| `HARNESS_TIMEOUT_SECONDS` | Service-side ceiling on one invocation. |
| `HARNESS_MAX_ITERATIONS` | Service-side ceiling on agent loop rounds. |

If AWS is missing, misconfigured or fails, the app shows what failed and how to fix it, and loads no findings.

## If something breaks

| Symptom | First check |
| --- | --- |
| Missing module | Activate `.venv`; rerun `pip install -r requirements.lock`. |
| "Harness ARN rejected" | Replace the placeholder with the real ARN; check the region. |
| No credentials | Use a valid profile or role. The offline demonstration needs none. |
| Live run appears stuck | Check `HARNESS_READ_TIMEOUT`; a stalled stream ends with an explained error. |
| Replay has no data | A live run must finish first. The offline demonstration does not create saved runs. |
| Upload rejected | The message lists each problem by row. Download the CSV template from Intake. |
| Table is cut off on a narrow window | Widen the window or use the table's own scroll bar. |

## Private local exports

For an authorized, local-only check the parser expects `process_qa.xlsx`, `compliance_qa.xlsx`, `experience_qa.xlsx` and `training_outline.xlsx` in a Git-ignored `data/` folder, with `QA_DATA_SOURCE=private` and `ALLOW_PRIVATE_QA_READ=1`. The switches are friction, not permission. Never commit those files or any output that contains private records. See [Security](SECURITY.md).
