# ResultsCX × Amazon AWS AI Competition 2026

### Agent QA Review | From a QA finding to targeted coaching and practice

**A failed QA item should lead to more than another score on a report.** Imagine a customer-service employee who discusses a member's plan update before completing the required identity-verification steps. An evaluator records the missed behavior, but a supervisor still has to interpret the evidence, decide what needs correcting, and prepare a useful coaching conversation. Repeat that process across employees and recurring QA issues, and the work between *finding a mistake* and *helping someone correct it* becomes a challenge of its own.

Our team built **Agent QA Review** to connect those steps. Python measures which scored behaviors are being missed and links the results to evaluator comments. Amazon Bedrock AgentCore can turn that evidence into a proposed explanation and intervention. A supervisor checks and edits the finding before the application prepares a focused coaching package: a learning objective, teaching points, a knowledge check, a guided exercise, a fictional customer scenario, and a branching roleplay script with coach notes.

**The value is the handoff:** one reviewed QA problem can travel from evidence to a specific practice activity without a supervisor having to assemble every training element from scratch. That lets the supervisor focus on deciding what the evidence means, checking the recommendation, and coaching the employee rather than starting each lesson plan from a blank page.

**At a glance:** Four-stage Streamlit workflow · AWS AgentCore `InvokeHarness` integration · Python-verified evidence citations · supervisor review and compliance flags · tailored coaching and branching scripts · pre-model personal-data masking · interactive business impact calculator · **50 passing offline tests**. [Explore the architecture](docs/ARCHITECTURE.md) · [Run the synthetic walkthrough](docs/SETUP.md) · [Review the privacy limits](docs/SECURITY.md) · [Verification report](docs/VERIFICATION.md).

[![Four-stage Agent QA Review workflow](docs/architecture-flow.svg)](https://raw.githubusercontent.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026/main/docs/architecture-flow.svg)

*From scored QA evidence to a supervisor-reviewed coaching plan. [Open the full-size workflow](https://raw.githubusercontent.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026/main/docs/architecture-flow.svg).*

I'm **George Beazer**, a senior cybersecurity student at **Kennesaw State University**. On September 17 and 18, 2026, I competed with teammates Kevin, Jackendy, Kylee, Jack, and Nelson in the ResultsCX and AWS AI College Competition.

This repository presents the competition application, the product idea behind it, the code I contributed to and later refined, and a public walkthrough that can be reviewed without ResultsCX data.

## The story in 90 seconds

Consider a customer-service representative who discusses a healthcare member's account before completing identity verification. The QA score captures the missed control, but a score alone doesn't teach the employee how to handle the next call. A supervisor still needs to examine the comments, determine what went wrong, and build a focused lesson and practice conversation.

**Agent QA Review closes that gap.** Python calculates QA patterns; an added privacy checkpoint masks recognizable personal identifiers before prompts or tool responses reach Bedrock. The AI drafts an evidence-linked diagnosis, but the supervisor checks it, changes the recommendation when necessary, and chooses what to coach. Failed identity/privacy controls also appear as **immediate review flags** independent of the AI's answer. Once a finding is approved, the workflow creates a coaching objective, a quiz, a guided exercise, and a branching practice script.

The last question is business impact: *how much supervisor preparation time could that save?* An interactive calculator lets the operator change weekly volume, manual time, AI-assisted time **including review**, labor cost, and working weeks. For example, **40 packages/week at 30 vs. 18 minutes** could free **8 supervisor hours/week**, or **400 hours/year**, valued at **$18,000/year** using a hypothetical $45/hour loaded rate. These are scenario assumptions—not observed savings, avoided fines, or a claim of reduced HIPAA incidents.

The product idea is simple: **identify a missed behavior, protect the data, escalate sensitive failures, let a human decide, and turn the decision into useful practice—with a transparent estimate of its possible value.**

## The core idea

> **Find the missed behavior. Check the evidence. Let a supervisor make the call. Turn the approved finding into focused coaching and practice.**

### One missed verification step, one focused coaching plan

The public demonstration includes an **invented** compliance evaluation in which an employee discusses a plan update before finishing verification. Here is how that kind of finding moves through the workflow:

1. **Identify the pattern.** The application reads scored QA line items and comments, calculates the defect rate in Python, and keeps the finding connected to its source record. It does not listen to call recordings or transcribe audio.
2. **Draft the diagnosis.** In authorized live mode, AI proposes what may explain the missed behavior, cites supporting evaluator comments, and suggests whether practice, instruction, coaching, or operational escalation is appropriate. That is a hypothesis for review, not a proven psychological or operational root cause.
3. **Put the supervisor in control.** The supervisor sees the observed evidence separately from the AI interpretation, can change the proposed cause and intervention, and chooses which findings actually warrant training.
4. **Build the practice.** For selected findings, the training workflow prepares an objective, instructions, a knowledge-check question with suggested answer and feedback, facilitator-guided practice, an imaginary member situation, scoring criteria, and a scripted conversation with alternative member reactions. The supervisor can use these materials in a coaching session.

The public synthetic walkthrough shows handwritten fictional outputs for this process. Bedrock generation is a separate live mode requiring an authorized AWS harness; neither the example nor its figures represent real ResultsCX employee performance.

## The problem we wanted to improve

A QA score is a starting point, not a coaching strategy. A supervisor may need to move between scored evaluations, written comments, training outlines, and coaching notes to answer four practical questions: **What happened? Does the evidence explain why? Is training the right response? What should the employee practice?**

Our application was designed to connect those decisions into one guided workflow:

**QA evidence → diagnosis draft → manager judgment → targeted coaching → practice**

The improvement we were trying to demonstrate was **workflow integration**. Instead of treating QA analytics, supervisor review, training design, and practice as disconnected activities, the same finding can move through the process while the human supervisor stays in control. This is particularly useful when the same QA behavior is missed across multiple evaluations and a generic refresher course would not address the specific issue.

This is not a claim that the prototype outperforms a named commercial QA platform. It is the product idea we were pitching: make the path from a QA finding to useful coaching faster, more focused, and easier to trace back to the original evidence.

## What the application is meant to do

### For supervisors and managers

The application is designed to help a supervisor:

1. See behavior level defect rates and the evaluator comments behind them.
2. Review AI proposed findings instead of starting from a blank page.
3. Separate **observed QA evidence** from the AI's **inferred explanation**.
4. Check which source records support a finding.
5. Edit the proposed root cause and recommended intervention.
6. Add supervisor notes and include or exclude findings.
7. Decide which issues actually need training.
8. Open targeted coaching and practice material for the approved issue.
9. Reopen saved results when a saved run exists.

The intention is to reduce the manual work between identifying a problem and preparing a focused coaching session without removing the manager from the decision.

### For employees and trainees

For an approved finding, the coaching workflow is designed to provide:

1. A clear learning objective.
2. Short targeted instruction.
3. A knowledge check with answer choices, a suggested answer, and feedback.
4. A guided exercise with trainee and facilitator instructions.
5. Observable success criteria.
6. A realistic fictional customer or healthcare member scenario.
7. A benchmark for what good performance should look like.
8. A branching roleplay where the simulated customer reacts differently depending on whether the target behavior is demonstrated.
9. Coach notes showing what to pay attention to during practice.

The purpose is not simply to tell an employee that a QA item was missed. It is to give the employee something specific to **learn, practice, and discuss with a supervisor**.

### For customers and healthcare members

The application is not a customer facing chatbot. The customer benefit is indirect: the concept is intended to help employees practice the behaviors that affect service quality, including clear explanations, documentation, verification, and handling realistic member situations.

Because some competition scenarios involved healthcare member services, the training workflow supports fictional scenarios involving benefits, claims, EOBs, care plans, provider outreach, and HIPAA verification.

## Why this approach could be useful

The central advantage is not simply producing an AI-written report. It is **moving from a verified QA issue to a usable, behavior-specific coaching activity in the same workflow**. Compared with a fragmented manual process, the application is intended to offer:

* **More focused coaching:** training is built around a specific scored behavior instead of generic customer service material.
* **Less repetitive preparation:** AI drafts an evidence-linked diagnosis and training materials so the manager has a structured starting point instead of preparing every exercise from scratch.
* **Human control:** supervisors can edit, reject, or exclude findings before training is generated.
* **Traceability:** findings can be tied back to specific QA record IDs and evaluator comments.
* **Consistent practice:** quizzes, exercises, fictional scenarios, scoring criteria, and branching roleplay scripts can follow the same learning objective.
* **Clearer fact versus inference:** the interface separates what evaluators actually observed from what the model believes may explain it.
* **Reusable review:** saved runs can be reopened instead of forcing every review to begin again.

### The business case: supervisor time, coaching consistency, and traceable decisions

In a large contact center, the cost of QA follow-up is not limited to grading the call. Someone also has to review the finding, decide what the employee needs, prepare the material, and conduct the coaching. Agent QA Review targets the **preparation and handoff** part of that work. It can assemble a draft coaching package for a selected behavior while keeping the evidence and supervisor decision visible.

The potential organizational value has three parts:

1. **Supervisor preparation time:** spend less effort assembling an initial lesson plan, quiz, and scenario from separate notes and documents.
2. **More specific practice:** focus a coaching session on the recorded behavior instead of automatically assigning a broad refresher course.
3. **Reviewable decisions:** preserve which QA finding led to the proposed intervention and what the supervisor approved.

The app now includes an **interactive, assumptions-based business calculator** on the first screen. It compares manual preparation hours with AI-assisted preparation **including human review**, calculates hours potentially freed per week/year, and translates that time into an estimated labor-capacity value. The editable example uses 40 packages/week, 30 vs. 18 minutes each, 50 working weeks, and $45/hour; that produces 400 hours and $18,000/year. It also shows a negative result when the assisted process is slower. A defensible *real* estimate would measure preparation time before and after an authorized pilot and validate the case volume, labor rate, and changes in quality outcomes. No real labor savings, defect reduction, or production performance have been measured here.

The project therefore demonstrates the workflow that could create value, together with a way to evaluate that value in a future authorized pilot. It does not claim that other QA products lack coaching features or that this prototype has already outperformed them.

## How the application works

I used Streamlit to connect four parts of the process. The dashboard lets supervisors move from QA findings to a specific coaching decision, while the AI handles the diagnosis draft and the training content for selected findings.

The [full-size architecture diagram](https://raw.githubusercontent.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026/main/docs/architecture-flow.svg) above illustrates how each stage passes the reviewed finding to the next. The SVG stays sharp when zoomed in.

| Stage | What happens |
| --- | --- |
| **1. QA intake** | I load scored evaluations, keep the evidence tied to record IDs, and calculate behavior-level defect rates in Python. The first screen also provides an interactive labor-hours calculator. The public walkthrough uses invented records. |
| **2. AI diagnosis** | A second sanitization checkpoint scrubs recognizable personal identifiers from the AI payload and final prompt before Bedrock can receive them; the tool callback uses the same safe payload. Bedrock proposes the observed problem, a possible root cause, confidence, and an intervention. Python checks citations against loaded records. |
| **3. Supervisor review** | Python rules immediately flag **every failed Compliance QA item** for human review, with higher priority for identity/privacy keyword matches, regardless of the model's conclusion. A supervisor can adjust the root cause and intervention, add notes, and include or exclude findings. The alert is in-app only; no external task is dispatched. |
| **4. Coaching and practice** | Selected findings become targeted instruction, a behavior-specific knowledge check with answer and feedback, a guided exercise with scoring criteria, a fictional customer scenario, and an agent/member roleplay script with branches. The app checks structural alignment, including whether the quiz references the target behavior, and can save results for read-only replay. |

The knowledge check displays questions and suggested answers; it does not grade a trainee's responses. The roleplay is a **generated practice script**, not a live customer conversation or an autonomous customer-facing agent. The AI workflow uses one AgentCore harness session with separate prompts for diagnosis and training. Compliance flags are rule-based **potential risks**, not verified incidents or declarations of legal violations.

`get_data.py` prepares records and calculates defect rates. `security_controls.py` performs a second, recursive privacy pass and detects potential critical compliance failures from scored records. `prompts.py` defines the diagnosis and coaching instructions. `harness_client.py` is the AWS boundary and parses the streamed AgentCore response. `pipeline.py` enforces privacy on final prompts and AI tool payloads, verifies citations, attaches metrics, manages reviewed findings, and checks generated modules. `business_impact.py` calculates hypothetical labor hours and cost scenarios. `app.py` brings the stages together in Streamlit. `main.py` provides terminal checks.

This is a **multi-stage AI workflow**, with a human review step. I do not describe it as six independently deployed agents because the code does not establish that.

## Product experience and feature vision

**Concept designs, not application captures:** These images show the broader coaching experience we wanted the application to communicate. They use **synthetic information** and are illustrative interface mockups rather than screenshots captured from the current `app.py` build. The decorative `LIVE CAPTURE` labels within the images are part of the mockup and do not indicate a recorded live session. The sample savings shown are not measured outcomes. The actual application presents a scripted roleplay, not an interactive conversation.

| Coaching and roleplay concept | Coaching focus and alignment concept |
| --- | --- |
| [![Roleplay interface mockup](docs/screenshots/agent-qa-roleplay-demo.webp)](docs/screenshots/agent-qa-roleplay-demo.webp) | [![Coaching focus mockup](docs/screenshots/agent-qa-coaching-focus-demo.webp)](docs/screenshots/agent-qa-coaching-focus-demo.webp) |
| Fictional branching dialogue, quiz and guided practice | Synthetic QA baseline, learning objective and alignment example |

The working Streamlit interface uses a four-stage layout. I kept the mockups because they communicate the intended experience: a manager moves from a QA issue into coaching, a knowledge check, guided practice, and realistic roleplay while keeping the training tied to the original problem. The labor-cost numbers **inside those mockups** are illustrations, not proof of savings. The **actual app now has a separate working, editable business impact calculator**, whose result changes with the assumptions entered. The images' limitations are explained in [Screenshot notes](docs/SCREENSHOTS.md).

## Healthcare privacy and security design

Because the use case can involve healthcare member service data, confidentiality is part of the application design rather than an afterthought.

| Control | Plain English purpose |
| --- | --- |
| **Synthetic data by default** | The public walkthrough runs without real customer, member, or employee records. |
| **Employee pseudonyms** | The private data path replaces known employee identities with codes before model use. |
| **Identifier masking** | The parser masks several common patterns such as emails, phone numbers, dates, provider references, and long identifiers. |
| **Pre-model sanitization checkpoint** | `security_controls.py` scrubs nested payload text and final training/diagnosis prompts, including recognizable emails, phones, SSNs, labeled account/member IDs, dates, and configured staff names. Bedrock's data tool uses the same filtered payload. |
| **Immediate in-app compliance flags** | In Supervisor Review, all failed Compliance evaluations are flagged by Python rules; identity/privacy keywords receive urgent priority. No external notification is sent. |
| **Private data read gate** | Private QA files are not read unless an explicit configuration switch is enabled. |
| **Cloud send gate** | The private data path is blocked from being sent to AWS unless a separate explicit authorization switch is enabled. |
| **Private output storage gate** | Derived private JSON is not written locally unless a separate storage switch is enabled. |
| **Credential separation** | AWS identifiers and credentials stay outside the source code and use environment configuration and Boto3's credential provider chain. |
| **Repository exclusions** | `.gitignore` keeps company workbooks, credentials, local environment files, real name lists, logs, and saved outputs out of Git. |
| **Evidence validation** | The pipeline checks record ID, behavior, and quoted text before labeling a diagnosis citation verified. |

These controls reflect **HIPAA aware confidentiality concerns and privacy minded development**, especially the goal of minimizing unnecessary exposure of healthcare related information.

They do **not** make the prototype HIPAA compliant. The masking is not certified de identification, saved JSON is not encrypted, and this repository cannot verify organizational AWS agreements, IAM policy, retention controls, or authorization to process protected health information.

See [Security](docs/SECURITY.md) for the implementation boundaries.

## My technical contributions

My contribution focused on helping turn the team's idea into a working technical workflow. I assisted with coding, troubleshooting, AWS integration, data handling, the Streamlit graphical interface, prompt workflow, and security minded handling of sensitive data. These are the specific areas I worked on:

1. **Development setup:** I configured our local GitHub project in VS Code on Windows 11 and worked through Python virtual environment and path problems using `uv`.
2. **Dependencies:** I resolved installation and import issues involving Boto3, OpenPyXL, pandas and requests during development. The public dependency list now contains only the packages the application uses.
3. **AWS SDK bug:** I fixed the type passed as `runtimeSessionId`. Boto3 expects a string; `str(uuid.uuid4())` produces the 36-character value used by our harness client.
4. **Streamlit UI:** I built and debugged parts of `app.py`, including moving QA findings into a supervisor review and coaching workflow.
5. **Data processing:** I investigated retrieval and parsing issues in `get_data.py` and helped connect the extracted evidence to the analysis stages.
6. **Prompt design:** I wrote and revised instructions separating observed evidence from inferred causes, structured JSON responses, supervisor review, and targeted training content.

I also worked on the privacy and security side of the application because the healthcare member service use case can involve sensitive information. The current public code keeps credentials outside source code, uses pseudonyms in the private data path, masks several common identifier patterns, and requires explicit controls before private QA data can be read, sent to AWS, or saved as derived output. In a later portfolio revision, I added **`security_controls.py`, a second sanitization layer at the Bedrock boundary, deterministic identity/privacy flags in Supervisor Review, and offline tests for both**. I also added the business calculator. These are **HIPAA aware confidentiality controls**, not a claim of HIPAA compliance. The masking is not certified de identification and saved JSON is not encrypted.

I worked with the team; I did not write every part of the application by myself. The September competition prototype and this later, public-safe cleanup are separate pieces of work.

## What you can run

| Mode | What it does | Needs AWS or private data? |
| --- | --- | --- |
| **Synthetic walkthrough** (default) | Runs the dashboard with nine invented QA line items and handwritten diagnosis/training examples | No |
| **Live Bedrock** | Uses `boto3` and AgentCore `InvokeHarness` to generate an analysis and coaching modules | Yes, an authorized harness and credentials |
| **Saved-run replay** | Reopens a completed diagnosis and training package with its original recorded approval, without collecting a new approval | No live AWS call, but a completed saved run must already exist |

The synthetic walkthrough is deliberately **not** presented as AI output, an actual ResultsCX evaluation, or measured business performance.

## Run it locally

I developed on Windows 11. I verified this public build with Python 3.12 and a locked dependency set. From PowerShell with Git and [`uv`](https://docs.astral.sh/uv/) installed:

```powershell
git clone https://github.com/BeazerJobs-USA/ResultsCX-Amazon-AWS-AI-Competition-2026.git
cd ResultsCX-Amazon-AWS-AI-Competition-2026

uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.lock

python -m unittest discover -s tests -v
python -m streamlit run app.py
```

Select **Synthetic walkthrough**, click **Load synthetic walkthrough**, review the sample findings, and use the review form to open the matching practice modules. No AWS credentials are needed.

For a terminal-only check, `python main.py --dry-run` builds the synthetic-data prompt without calling AWS. The `--live` flag is required for terminal model invocation, and the UI has a separate Live mode. I explain the environment variables and approval requirements in [Setup](docs/SETUP.md).

## AWS integration and security

The actual application uses the **Bedrock AgentCore `InvokeHarness` API**, not the separate `InvokeAgentRuntime` request shape. In `harness_client.py`, Boto3 sends the harness ARN, a string runtime session ID, and messages, then parses streamed content and tool-use events.

```python
session_id = str(uuid.uuid4())
response = client.invoke_harness(
    harnessArn=approved_harness_arn,
    runtimeSessionId=session_id,
    messages=[{"role": "user", "content": [{"text": sanitized_prompt}]}],
)
```

This excerpt shows the API shape, not a complete standalone script. The code gets `HARNESS_ARN` from local configuration and credentials from Boto3's AWS provider chain. My `.env.example` contains placeholders; no real keys are included.

I kept the original private QA exports, real-name masking list, access-key files, saved run outputs and unused generated workbook parser out of the public repository. The default is synthetic data. Private-data reading, cloud transmission and local output storage have separate opt-in controls; those switches are **not substitutes for authorization or a privacy review**.

I also tightened the citation check. A citation is marked verified only when its ID exists, it belongs to the diagnosed behavior, and its quoted text matches that record. Alignment checking is still a structural and keyword heuristic, not proof that training is effective. See [Security](docs/SECURITY.md) and [Architecture](docs/ARCHITECTURE.md).

## Verification and limits

I run the offline tests with:

```powershell
python -m unittest discover -s tests -v
```

**50 offline tests pass** in a fresh Python 3.12 environment using `requirements.lock`. They cover synthetic record calculations, stream parsing, interrupted AWS streams, bounded tool-use rounds, the Boto3 request shape, supervisor-to-training flow, real Streamlit script progression and calculator input changes, replay behavior, citation checks including sanitized quotes, behavior-matched quiz examples, malformed model output, private-data guards, identifier masking, compliance-alert rules, and business-math scenarios. They do **not** invoke AWS. Local-only structure checks on separately provided workbooks were also performed without including those files in the repository. The live harness and end-user browser behavior require separate authorized/environment testing. See [Verification](docs/VERIFICATION.md).

The dashboard examples use invented cases. No post-training QA comparison has been supplied, so I don't claim an actual reduction in defects or real labor savings. This is a student competition project, not a deployed ResultsCX system.

## Competition rubric

| Criterion | Weight |
| --- | ---: |
| Diagnostic Accuracy | 25% |
| End-to-End Generation | 25% |
| Training Alignment | 20% |
| Practice Scripting | 10% |
| Feasibility | 10% |
| Presentation | 10% |

Those are the competition's rubric weights, **not our awarded scores**. I have not published a placement.

## Repository map

```text
app.py                     Streamlit dashboard
main.py                    Terminal check and live runner
get_data.py                Synthetic/private data loading and deterministic metrics
security_controls.py       Pre-model identifier masking and compliance alert rules
business_impact.py         Interactive calculator's scenario math
prompts.py                 Diagnosis and coaching instructions
harness_client.py          Boto3 InvokeHarness integration and stream parsing
pipeline.py                Citations, supervisor handoff, generation and alignment
sample_walkthrough.py      Handwritten offline demonstration (not model output)
sample_data/               Invented public QA records
tests/                     Offline unit and app-flow tests
.streamlit/config.toml     Dashboard theme
docs/                      Architecture, security, setup, screenshots, contributions
requirements.txt           Supported dependency ranges
requirements.lock          Tested Python 3.12 versions
```

## Team and next steps

My teammates were **Kevin, Jackendy, Kylee, Jack, and Nelson**. We worked on the same competition project, and I have kept the contributions I can personally account for separate from the team's overall work.

The next steps are to capture the current Streamlit workflow, finish authorized testing against the live AWS harness, and compare the coaching output with actual follow-up QA results if those become available.

*ResultsCX and AWS are trademarks of their owners. This is an independent student-team project, not an official ResultsCX or AWS product.*

