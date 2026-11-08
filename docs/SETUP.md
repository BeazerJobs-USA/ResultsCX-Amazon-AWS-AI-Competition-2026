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

The default **Synthetic walkthrough** always uses `sample_data/synthetic_qa.json`, regardless of any local private-data setting. Click **Load synthetic walkthrough**, inspect the findings, then approve or edit the fictional review form to see the sample coaching modules. The verification and documentation cases have different quizzes and practice scenarios. This is not a live generative-AI result.

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

## If something breaks

| Symptom | First check |
| --- | --- |
| Missing module | Confirm VS Code and PowerShell use `.venv`; rerun `uv pip install -r requirements.lock`. |
| Missing synthetic data | Confirm `sample_data/synthetic_qa.json` exists and `QA_DATA_SOURCE=synthetic`. |
| Harness ARN rejected | Replace the placeholder with the authorized harness ARN; verify the region. |
| No AWS credentials | Use a valid profile or role. The public walkthrough requires none. |
| Boto3 session type error | `runtimeSessionId` needs `str(uuid.uuid4())`, not a UUID object. |
| A replay has no data | Complete an authorized Live-mode flow to save diagnosis, approval, and training output. The handwritten walkthrough does not create saved runs. |

Replay displays a completed saved diagnosis and training package with its original recorded approval when one exists. It does not generate new modules or record a new supervisor decision. A diagnosis-only run cannot be replayed as a completed training package.

The private-data path is described in [Security](SECURITY.md). I keep it disabled unless the dataset and processing environment are approved for that use.

