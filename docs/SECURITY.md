# Security Practices and Publication Checklist

## Scope

This project touches simulated healthcare-related customer-service QA workflows. That raises security and privacy considerations even in an academic prototype. The material below is a **control checklist and design guidance**, not a verified attestation of implemented controls or HIPAA compliance.

## Configuration and credentials

| Item | Expected location | Public repository rule |
| --- | --- | --- |
| `HARNESS_ARN` / AgentCore runtime ARN | Local process environment or approved deployment configuration | Use placeholders; do not publish account-specific resource identifiers unnecessarily |
| AWS region | Environment or approved config | Safe to show an example region; verify real region against deployment |
| `AWS_PROFILE` | Local environment/profile config | Name only; never copy the profile's credential files |
| AWS access key, secret key, session token | AWS CLI/SDK provider chain, temporary role, or identity federation | Never commit or screenshot values |
| ResultsCX spreadsheets and source evidence | Authorized private storage only | Do not publish without written clearance and full redaction |
| Generated outputs and app logs | Controlled local or approved storage | Exclude raw cases, identities, prompts, and confidential content |

The `.gitignore` excludes `.env`, local credential files, private data directories, logs, and common spreadsheet exports. **A `.gitignore` is not a security boundary**: it does not remove information already committed and does not replace a pre-push inspection.

## Secure data flow

1. **Minimize input:** supply the model with only fields necessary for diagnosis. Replace names, member numbers, phone numbers, and medical detail with controlled synthetic placeholders.
2. **Validate before invocation:** check required keys and expected numeric ranges. Do not accept arbitrary model-produced values as verified measurements.
3. **Constrain permissions:** authorize `bedrock-agentcore:InvokeAgentRuntime` only for the approved runtime ARN when possible; inspect policy boundaries with the account owner.
4. **Separate sources:** preserve QA evidence, model inference, human supervisor decision, and generated coaching as distinct records.
5. **Handle failures safely:** return actionable but nonsensitive errors; never print credentials, raw PII, or full HTTP payloads in console traces.
6. **Verify output:** treat generated scripts as training drafts that need subject-matter review before use.
7. **Review retention:** saved-run JSON or caches may contain sensitive content. Do not assume locally saved output is encrypted; apply appropriate controls or use synthetic-only data.

## Current assurance status

| Security claim | Status in this public repository |
| --- | --- |
| Config examples avoid embedded secrets | **Present in reference code and templates** |
| Private data and common secret files ignored | **Present in `.gitignore`** |
| Reference validator checks schema and ranges | **Present in `examples/`** |
| IAM least privilege configured on AWS account | **Not independently verified** |
| Private runtime inputs encrypted at rest | **Not independently verified** |
| Fernet or application-layer encryption implemented | **Not verified; do not claim** |
| Production HIPAA controls/compliance | **Not established; do not claim** |
| Full original application dependency or penetration tests | **Not available in this public release** |

## Before publishing original competition source

- [ ] Verify that the team and organizers authorize distribution of the source and visuals.
- [ ] Remove ResultsCX-only documents, QA workbooks, notes, and other NDA material.
- [ ] Rotate any credentials that were ever copied into files or commit history.
- [ ] Run secret scanning and inspect the entire new commit diff, including notebooks and media metadata.
- [ ] Substitute sanitized, explicitly synthetic fixtures for actual member or employee records.
- [ ] Document tested IAM permissions, app configuration, and dependency versions without exposing values.
- [ ] Add real tests and execution evidence before claiming verified defenses or measured outcomes.

If credentials are accidentally published, treat them as compromised and revoke/rotate them immediately. Deleting a file in a later commit alone does **not** remove the leaked value from history.

Primary references: [AWS Boto3 credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html) and [Bedrock AgentCore Runtime sessions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html).

