# Python Reference Examples

These files are **new, independently written examples** demonstrating safe coding patterns related to the ResultsCX/AWS competition. They are **not** recovered original code and do not prove exactly what was deployed in September 2026.

| File | Purpose |
| --- | --- |
| `bedrock_agentcore_reference.py` | Example of Boto3 Runtime invocation with environment-sourced ARN, default AWS credential chain, JSON payload and string session ID |
| `qa_data_validation_reference.py` | Small schema and range validator for a **synthetic** QA case |

## Run safe offline checks

From repository root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q examples tests
```

The validator requires no cloud resources. The AgentCore example **does not invoke AWS automatically**; calling its function requires an authorized runtime, correct region, valid credentials, and may incur AWS charges. Never use private ResultsCX data or real member details in a portfolio demonstration.

