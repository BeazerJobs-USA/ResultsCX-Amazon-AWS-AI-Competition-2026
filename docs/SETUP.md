# Setup and usage

I built this project on Windows 11 with VS Code and Python. I verified the public build with Python 3.12 and `requirements.lock`. The public mode can run without an AWS account.

## Windows 11 with uv

```powershell
git clone https://github.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026.git
cd ResultsCX-Amazon-AWS-AI-Competition-2026

uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.lock

python -m unittest discover -s tests -v
python -m streamlit run app.py
```

The default **Synthetic walkthrough** always uses `sample_data/synthetic_qa.json`, regardless of any local private-data setting. Click **Load synthetic walkthrough**, inspect the findings, then edit the fictional review form as needed and select **Save review and prepare selected training**. The verification and documentation cases have different quizzes and practice scenarios. This is not a live generative-AI result.

## Terminal checks

```powershell
python get_data.py
python main.py --dry-run
python main.py
```

These default to the synthetic dataset, and none of them call Bedrock. The source prints metric counts and prompt length, not raw private prompt content.

## Optional live AWS mode

I only use this with a harness and account the team is authorized to access:

1. Copy `.env.example` to `.env` and replace the harness placeholder and profile name with approved local settings.
2. Configure an AWS profile, IAM Identity Center, or an assumed role. Do not put access keys in Git.
3. Keep `QA_DATA_SOURCE=synthetic` when evaluating the public example.
4. In the UI choose **Live — call Bedrock**, or deliberately run `python main.py --live`.

`HARNESS_ARN` is a **harness ARN**. The client calls `invoke_harness`, not `invoke_agent_runtime`. The current SDK contract expects `harnessArn`, a string `runtimeSessionId` and `messages`. AWS permission and region checks depend on the actual deployment.

`HARNESS_READ_TIMEOUT` is optional and defaults to 120 seconds. It sets how long a single AWS stream read may be idle before the SDK raises an error. It does **not** impose a hard end-to-end deadline on multiple training modules. No live AWS availability is implied by the offline tests.

## If something breaks

| Symptom | First check |
| --- | --- |
| Missing module | Confirm VS Code and PowerShell use `.venv`; rerun `uv pip install -r requirements.lock`. |
| Missing synthetic data | Confirm `sample_data/synthetic_qa.json` exists and `QA_DATA_SOURCE=synthetic`. |
| Harness ARN rejected | Replace the placeholder with the authorized harness ARN; verify the region. |
| No AWS credentials | Use a valid profile or role. The public walkthrough requires none. |
| Boto3 session type error | `runtimeSessionId` needs `str(uuid.uuid4())`, not a UUID object. |
| Live model appears stuck | Check the current Stage 2/4 status; after a stream read timeout the UI reports an error. Check `HARNESS_READ_TIMEOUT` and the authorized harness logs. |
| A replay has no data | Complete a review in authorized Streamlit Live mode. Older separate JSON files cannot establish one reviewed run. The handwritten walkthrough and terminal test mode do not publish replay bundles. |

Replay opens the latest published Streamlit Live review as one bundle in `outputs/latest_run.json`. It can show complete or partial training, or a review that selected no training. If all selected modules fail, the prior saved replay remains available. The Live screen offers a retry for failed modules and keeps successful ones. A diagnosis alone does not publish a replay. A replay uses the original reviewer entry, with no new decisions or model calls.

The private-data path is described in [Security](SECURITY.md). I keep it disabled unless the dataset and processing environment are approved for that use.

For an authorized *local-only* private-data intake check, the parser expects the following files in a separate, Git-ignored `data/` folder: `process_qa.xlsx`, `compliance_qa.xlsx`, `experience_qa.xlsx`, and `training_outline.xlsx`. The upstream exports may have longer filenames, so rename local copies to these expected names. The private-data switches are not permission to upload real member information to AWS. **Never commit the original exports or any result containing private records.**

