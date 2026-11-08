# Security and data handling notes

The healthcare member service use case made confidentiality an important design requirement. The goal of the public version is simple: use synthetic data by default, minimize sensitive information before model use, require explicit choices before private data can be read or sent to AWS, and keep credentials and company files out of the repository.

These are **HIPAA aware privacy and confidentiality controls**, not a claim of HIPAA compliance. I configured the default workflow to use invented QA records, so the application runs without company files or AWS access. The private data parser has limited masking, and those regex checks do not establish that all personal information was removed.

## Controls present in the public code

| Area | What I can point to |
| --- | --- |
| Public data | `sample_data/synthetic_qa.json` contains invented evaluation records and no real member or employee names. |
| Input selection | `QA_DATA_SOURCE` defaults to `synthetic`; reading local private workbooks requires `QA_DATA_SOURCE=private` and `ALLOW_PRIVATE_QA_READ=1`. |
| Public demo isolation | Selecting the Streamlit synthetic walkthrough explicitly forces the synthetic fixture, even if a developer's local `QA_DATA_SOURCE` is set to `private`. |
| Cloud transmission | `pipeline.ensure_cloud_upload_authorized()` checks reported integrity failures and blocks the private-data path unless `ALLOW_PRIVATE_CLOUD_SEND=1` is set. |
| Local outputs | Diagnosis, approval, and training JSON are saved for synthetic runs. Private derived output is not written unless `ALLOW_PRIVATE_OUTPUT_STORAGE=1`. |
| Credential handling | `harness_client.py` reads `HARNESS_ARN` from the local environment and lets Boto3 resolve credentials using its provider chain. |
| Version control | `.gitignore` excludes `.env`, credentials, company workbooks, real-name lists, logs and `outputs/`. |
| Evidence checks | `pipeline.py` checks record ID, behavior, and quoted text before marking a diagnosis citation verified. |

## Limits and risks

1. **Privacy:** The optional private parser pseudonymizes employee names and masks some common identifier patterns. It cannot detect every name, relationship, identifier, or sensitive statement. There is no certified de-identification process.
2. **Confidentiality:** Original ResultsCX workbooks and the real-name list were left out. I would need permission and a full human review before using any restricted material outside its approved environment.
3. **Storage:** Saved JSON is ordinary plaintext. I have not implemented or verified Fernet encryption, encrypted local storage, or secure enterprise retention.
4. **IAM:** AWS docs state an InvokeHarness caller may need both `bedrock-agentcore:InvokeHarness` and `bedrock-agentcore:InvokeAgentRuntime`. I cannot verify the team's deployed policy from this source ZIP. Least privilege remains a deployment responsibility.
5. **Model content:** A response can still be inaccurate or unsafe even if its JSON parses. A validated citation is not proof that the inferred root cause is correct. Human review remains essential.
6. **Regulation:** I am not claiming HIPAA compliance or authorization to process actual protected health information.
7. **Reviewer identity:** The Streamlit reviewer-name field is not an authentication system. Its saved approval is a prototype workflow record, not an enterprise audit trail or proof of organizational authorization.

The three private-data switches are deliberate friction, **not permission**. An authorized owner must still decide whether the input, AWS account, contract, and retention policy are appropriate.

## Before allowing a private-data run

- Confirm in writing that the specific dataset and cloud processing are permitted.
- Check the full input and prompt for names, account IDs, member details, provider data, and proprietary instructions.
- Use temporary AWS credentials or an approved profile; do not paste keys into code or Git.
- Review IAM access to the particular harness and underlying runtime.
- Decide whether derived JSON may be stored. If not, leave `ALLOW_PRIVATE_OUTPUT_STORAGE` unset.
- Recheck any exported Markdown/JSON before sharing it beyond the authorized team.

Reference: [AWS AgentCore harness permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-security.html) and [Boto3 credential providers](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).


