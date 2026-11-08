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

