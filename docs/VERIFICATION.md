# Verification and limitations

This page separates what was tested from what wasn't. All tests use invented data and fake network responses.

## Reproduce

```bash
pip install -r requirements.lock
python -m unittest discover -s tests -v
python main.py --dry-run
streamlit run app.py
```

**109 tests pass** (Python 3.13, Streamlit 1.67, locked versions). The browser tests need Playwright and Chrome and skip themselves otherwise.

| File | Tests | Runs |
| --- | --- | --- |
| `tests/test_workflow_logic.py` | 50 | Plain Python |
| `tests/test_streamlit_app.py` | 15 | Real Streamlit through `streamlit.testing.v1.AppTest` (headless, no browser) |
| `tests/test_browser.py` | 3 | Real server and headless Chrome through Playwright |
| `tests/test_offline.py` | 29 | Stream parsing, request shape, citations, replay, private-data guards |
| `tests/test_security_business.py` | 12 | Masking, flags, calculator |

## Feature by feature

| Feature | Tested | How | Not covered |
| --- | --- | --- | --- |
| Intake, filters, readiness checks | Yes | Logic tests; AppTest | Very large files |
| Upload validation (bad file rejected, template accepted, size limit) | Yes | Logic tests; browser test for a rejected upload | A good upload through the browser |
| Masking before model calls | Yes | Logic and offline tests | Real data; coverage of every identifier type |
| Risk rules and flag actions | Yes | Logic tests; AppTest for a dismissal | Legal meaning of any level |
| Role permissions | Yes | Logic tests | Real authentication does not exist |
| Review decisions, note rules, original-vs-final record | Yes | Logic tests; AppTest; browser | |
| Audit chain, tampering detection | Yes | Logic tests; AppTest | Protection against rewriting the whole chain |
| Coaching package generation | Yes | AppTest; browser | Live model output |
| Knowledge check grading, retry, unanswered refusal | Yes | Logic tests; AppTest; browser (unanswered) | |
| Roleplay branching | Yes | Logic tests; AppTest for both a correct and a wrong choice | Full multi-step runs through the UI |
| Guided practice, completion tracking, follow-up date rules | Partly | Logic tests | UI forms for these |
| Referral simulation (validation, approvals, never persisted) | Yes | Logic tests; AppTest | |
| Efficiency estimate and input validation | Yes | Logic tests; AppTest | |
| Live mode without configuration | Yes | AppTest: no findings appear, an explanation is shown | |
| Live Bedrock call | **No** | Request shape, tool rounds, malformed and interrupted streams tested with fakes | **A real call with real credentials** |
| Replay | Partly | Offline tests with simulated saved runs | A run saved by a real live call |
| Table fullscreen control | Yes, once by hand-run script (not in the suite) | On the Intake table in headless Chrome it enlarged the table to nearly the full window and Escape restored it, with no error | Not repeated on other tables |
| Table search, column picker, CSV download | **No** | These are Streamlit's own toolbar controls and were not exercised | The CSV download carries whatever the table shows, which is already masked |
| Phones and other browsers | **No** | | |
| Accessibility beyond icon-plus-word status indicators | **No** | No screen-reader or contrast audit was done | |

## What I found and fixed while testing

- A table of related flags overflowed its card at normal widths; it now spans the full width.
- Two six-column tables overflowed at about 1000 px; they now use a scrolling table.
- A selector's label function read session state when called outside a normal run, which broke testing and was fragile. It now looks labels up in a dictionary built beforehand.
- A failed regeneration of a coaching package could discard the earlier package. It now replaces only the regenerated modules.

## Boundaries

- The workflow is four stages around one harness, not independently deployed agents.
- The inputs are scored QA items and evaluator comments. The app does not hear or transcribe calls.
- The roleplay is a decision tree with fixed lines. It is not a live conversation.
- Roles are simulated. Referrals are simulated. No HR or ticketing system is contacted.
- The calculator estimates capacity from assumptions. It isn't measured savings.
- None of this has been run against the team's live AWS deployment.
