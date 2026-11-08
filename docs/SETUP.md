# Local Development Setup (Windows 11)

This is the **intended developer workflow** based on participant notes. The original application code and dependency lockfile have **not** been released here, so these commands are a preparation guide and will **not** launch the complete app until the source is added.

## Prerequisites

- Windows 11, VS Code, Git, and Python supported by the eventual release.
- [`uv` Python package/environment manager](https://docs.astral.sh/uv/).
- An approved AWS account/identity and access to the specific Bedrock AgentCore runtime if using real inference.
- Written authorization before using any ResultsCX materials or proprietary workloads.

## Setup

```powershell
git clone https://github.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026.git
cd ResultsCX-Amazon-AWS-AI-Competition-2026

uv venv
.\.venv\Scripts\Activate.ps1

# Illustrative dependency list; install from a future reviewed lockfile when available.
uv pip install streamlit boto3 openpyxl pandas requests

# Select the .venv Python interpreter in VS Code.
python --version
python -c "import boto3, pandas, openpyxl, requests, streamlit; print('Imports OK')"
```

### Environment configuration

- `.env.example` documents nonsecret configuration names but does **not** automatically load into `os.environ`.
- Set environment values in the current PowerShell session or in a securely managed launch configuration.
- Prefer `aws configure sso` or approved short-lived IAM credentials. Boto3 resolves credentials from its usual provider chain.
- Configure `HARNESS_ARN` with the actual authorized Runtime ARN only in the local environment. Do **not** publish it or copy credential material into source.

```powershell
$env:AWS_PROFILE = "your-approved-profile"
$env:AWS_DEFAULT_REGION = "us-east-1"  # Example; use actual deployment region
$env:HARNESS_ARN = "arn:aws:bedrock-agentcore:REGION:ACCOUNT:runtime/APPROVED-RUNTIME"
```

Once the team publishes approved source including `app.py`, the expected UI command is `streamlit run app.py`. Until then, only the isolated examples/tests in this repository can be exercised.

## Frequent debugging lessons from the competition

| Symptom | Check |
| --- | --- |
| `ModuleNotFoundError` | Is VS Code using the `.venv` interpreter? Were packages installed into that environment? |
| File not found for workbook | Check working directory and paths; never solve by uploading restricted input data |
| `runtimeSessionId` parameter type issue | Pass `str(uuid.uuid4())`, not a `uuid.UUID` object |
| AWS access failure | Confirm correct profile, role permissions, region, and runtime; avoid printing resolved credentials |
| Model call works, UI content stale | Inspect Streamlit session-state transitions and data structure passed to renderer |
| No AWS access after event | Use sanitized saved-run content; clearly mark that mode as offline demonstration |

## Testing reference code

The current repository includes only educational reference examples. Without access to AWS or private QA datasets, you can run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q examples tests
```

