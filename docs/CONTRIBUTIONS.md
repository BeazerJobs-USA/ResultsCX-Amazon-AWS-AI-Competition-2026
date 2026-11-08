# My contributions and development notes

I'm George Beazer, a senior cybersecurity student at KSU. This was a six-person academic competition project. I did the technical architecture and integration work described below, while other teammates also contributed to the application and presentation.

| Area | What I worked on | Relevant code |
| --- | --- | --- |
| Windows/VS Code setup | Configured the local GitHub project, Python virtual environment, working directories and package installation with `uv` | `requirements.txt`, setup instructions |
| AWS integration | Fixed the UUID type passed to `runtimeSessionId` and troubleshot Boto3 calls | `harness_client.py`, `main.py` |
| UI | Built and debugged how QA findings, supervisor input and training sections move through Streamlit | `app.py` |
| Data | Investigated Excel retrieval and data-processing issues | `get_data.py` |
| Prompt design | Developed the stage-specific instructions and supervisor handoff logic | `prompts.py`, `pipeline.py` |
| Security-minded development | Worked on separating local configuration, keeping names out of model prompts, and checking the data being submitted | `.env.example`, `get_data.py`, `pipeline.py` |

The updated public version also includes synthetic sample data, tighter citation checks, and a runnable offline walkthrough. I treat that as follow-up portfolio cleanup rather than implying all changes existed during the two-day competition.

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

