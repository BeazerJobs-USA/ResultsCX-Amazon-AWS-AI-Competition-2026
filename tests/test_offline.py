"""
Offline tests - no AWS access needed.   Run from the repo folder:   python -m unittest -v

They cover:
  * Stage 1 data pipeline (Excel -> masked records -> metrics -> payload with no real names)
  * Stream parsing (text, fragmented tool input, error events, malformed events)
  * The exact request shape sent to invoke_harness, validated against the official AWS API definition
  * Diagnosis / training JSON parsing and the citation + alignment validators
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import harness_client as hc  # noqa: E402
import pipeline  # noqa: E402
import get_data  # noqa: E402
import sample_walkthrough  # noqa: E402


def ev(**kwargs):
    return dict(kwargs)


class FakeStream(list):
    pass


class FakeClient:
    """Stands in for boto3's bedrock-agentcore client. Validates every request against the real
    service model (botocore ships the API definition) and replays scripted event streams."""

    def __init__(self, scripted_streams):
        self.scripted = list(scripted_streams)
        self.requests = []
        import boto3
        from botocore.validate import ParamValidator

        real = boto3.client(
            "bedrock-agentcore",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        self.input_shape = real.meta.service_model.operation_model(
            "InvokeHarness"
        ).input_shape
        self.validator = ParamValidator()

    def invoke_harness(self, **kwargs):
        report = self.validator.validate(kwargs, self.input_shape)
        if report.has_errors():
            raise AssertionError(
                "Request does not match the AWS API definition:\n"
                + report.generate_report()
            )
        self.requests.append(kwargs)
        return {"stream": FakeStream(self.scripted.pop(0))}


TEXT_STREAM = [
    ev(messageStart={"role": "assistant"}),
    ev(contentBlockStart={"contentBlockIndex": 0, "start": {}}),
    ev(contentBlockDelta={"contentBlockIndex": 0, "delta": {"text": "Hello "}}),
    ev(contentBlockDelta={"contentBlockIndex": 0, "delta": {"text": "world"}}),
    ev(contentBlockStop={"contentBlockIndex": 0}),
    ev(messageStop={"stopReason": "end_turn"}),
    ev(
        metadata={
            "usage": {"inputTokens": 12, "outputTokens": 3, "totalTokens": 15},
            "metrics": {"latencyMs": 40},
        }
    ),
]

TOOL_STREAM = [
    ev(messageStart={"role": "assistant"}),
    ev(
        contentBlockDelta={
            "contentBlockIndex": 0,
            "delta": {"text": "Let me fetch the data."},
        }
    ),
    ev(contentBlockStop={"contentBlockIndex": 0}),
    ev(
        contentBlockStart={
            "contentBlockIndex": 1,
            "start": {"toolUse": {"toolUseId": "tu_1", "name": "get_all_data"}},
        }
    ),
    # the input arrives as FRAGMENTS - the old code kept only the last one and crashed
    ev(
        contentBlockDelta={
            "contentBlockIndex": 1,
            "delta": {"toolUse": {"input": '{"sec'}},
        }
    ),
    ev(
        contentBlockDelta={
            "contentBlockIndex": 1,
            "delta": {"toolUse": {"input": 'tion": "a'}},
        }
    ),
    ev(
        contentBlockDelta={
            "contentBlockIndex": 1,
            "delta": {"toolUse": {"input": 'll"}'}},
        }
    ),
    ev(contentBlockStop={"contentBlockIndex": 1}),
    ev(messageStop={"stopReason": "tool_use"}),
    ev(
        metadata={
            "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            "metrics": {"latencyMs": 90},
        }
    ),
]


class StreamParsingTests(unittest.TestCase):
    def test_text_accumulates_and_usage_is_captured(self):
        chunks = []
        turn = hc.parse_stream(TEXT_STREAM, on_text=chunks.append)
        self.assertEqual(turn.text, "Hello world")
        self.assertEqual(chunks, ["Hello ", "world"])
        self.assertEqual(turn.stop_reason, "end_turn")
        self.assertEqual(
            (turn.input_tokens, turn.output_tokens, turn.latency_ms), (12, 3, 40)
        )
        self.assertEqual(turn.unexpected_events, [])

    def test_fragmented_tool_input_is_joined(self):
        turn = hc.parse_stream(TOOL_STREAM)
        self.assertEqual(len(turn.tool_uses), 1)
        self.assertEqual(turn.tool_uses[0].name, "get_all_data")
        self.assertEqual(turn.tool_uses[0].input, {"section": "all"})
        self.assertEqual(turn.stop_reason, "tool_use")

    def test_empty_tool_input_becomes_empty_dict(self):
        stream = [
            ev(
                contentBlockStart={
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "t", "name": "get_all_data"}},
                }
            ),
            ev(contentBlockStop={"contentBlockIndex": 0}),
            ev(messageStop={"stopReason": "tool_use"}),
        ]
        turn = hc.parse_stream(stream)
        self.assertEqual(turn.tool_uses[0].input, {})

    def test_error_events_are_raised_not_swallowed(self):
        for key in (
            "validationException",
            "internalServerException",
            "runtimeClientError",
        ):
            stream = [
                ev(**{key: {"message": "boom", "reason": "FieldValidationFailed"}})
            ]
            with self.assertRaises(hc.HarnessError) as ctx:
                hc.parse_stream(stream)
            self.assertIn("boom", str(ctx.exception))

    def test_malformed_events_do_not_crash(self):
        stream = [
            "not a dict",
            ev(contentBlockDelta={}),  # no delta
            ev(contentBlockDelta={"delta": {"somethingNew": 1}}),  # unknown delta
            ev(weirdEvent={"x": 1}),
            ev(contentBlockDelta={"contentBlockIndex": 0, "delta": {"text": "ok"}}),
            ev(messageStop={}),
        ]
        turn = hc.parse_stream(stream)
        self.assertEqual(turn.text, "ok")
        self.assertGreaterEqual(len(turn.unexpected_events), 3)


class SessionTests(unittest.TestCase):
    CONFIG = {
        "harness_arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:harness/agent_harness-abcdefghij",
        "region": "us-east-1",
        "max_tokens": 4000,
        "model_id": None,
    }

    def test_session_id_is_a_valid_string(self):
        sid = hc.new_session_id()
        self.assertIsInstance(sid, str)
        self.assertGreaterEqual(len(sid), 33)

    def test_tool_round_trip_and_request_shape(self):
        client = FakeClient([TOOL_STREAM, TEXT_STREAM])
        calls = []
        session = hc.HarnessSession(
            tools={"get_all_data": lambda **kw: calls.append(kw) or {"ok": True}},
            client=client,
            config=self.CONFIG,
        )
        text, turn = session.ask("analyse", system_prompt="You are a test.")
        self.assertEqual(text, "Let me fetch the data.Hello world")
        self.assertEqual(calls, [{"section": "all"}])
        self.assertEqual(len(client.requests), 2)
        first, second = client.requests
        self.assertIsInstance(first["runtimeSessionId"], str)
        self.assertEqual(
            first["runtimeSessionId"], second["runtimeSessionId"]
        )  # same session throughout
        self.assertEqual(first["systemPrompt"], [{"text": "You are a test."}])
        self.assertEqual(first["maxTokens"], 4000)
        self.assertEqual(second["messages"][0]["role"], "assistant")
        self.assertEqual(
            second["messages"][0]["content"][1]["toolUse"]["input"], {"section": "all"}
        )
        self.assertEqual(
            second["messages"][1]["content"][0]["toolResult"]["status"], "success"
        )
        self.assertEqual(session.total_input_tokens, 112)

    def test_unknown_tool_returns_error_result(self):
        stream = [
            ev(
                contentBlockStart={
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "t9", "name": "mystery"}},
                }
            ),
            ev(contentBlockStop={"contentBlockIndex": 0}),
            ev(messageStop={"stopReason": "tool_use"}),
        ]
        client = FakeClient([stream, TEXT_STREAM])
        session = hc.HarnessSession(client=client, config=self.CONFIG)
        text, _ = session.ask("hi")
        self.assertEqual(text, "Hello world")
        self.assertEqual(
            client.requests[1]["messages"][1]["content"][0]["toolResult"]["status"],
            "error",
        )

    def test_truncated_answer_is_reported(self):
        stream = [
            ev(
                contentBlockDelta={"contentBlockIndex": 0, "delta": {"text": "partial"}}
            ),
            ev(messageStop={"stopReason": "max_tokens"}),
        ]
        session = hc.HarnessSession(client=FakeClient([stream]), config=self.CONFIG)
        with self.assertRaises(hc.HarnessError) as ctx:
            session.ask("hi")
        self.assertIn("HARNESS_MAX_TOKENS", str(ctx.exception))

    def test_bad_arn_is_explained(self):
        import os

        old = os.environ.get("HARNESS_ARN")
        os.environ["HARNESS_ARN"] = "arn:aws:not-right"
        try:
            with self.assertRaises(hc.HarnessError) as ctx:
                hc.get_config()
            self.assertIn("HARNESS_ARN", str(ctx.exception))
        finally:
            if old is None:
                os.environ.pop("HARNESS_ARN", None)
            else:
                os.environ["HARNESS_ARN"] = old


class DataPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = get_data.get_all_data()

    def test_counts_reconcile(self):
        t = self.data["totals"]
        self.assertEqual(
            t["line_items"], sum(b["evaluated"] for b in self.data["behaviors"])
        )
        self.assertEqual(
            t["failed_items"], sum(b["failed"] for b in self.data["behaviors"])
        )
        self.assertTrue(
            all(ok for _, ok, _ in self.data["checks"]), self.data["checks"]
        )

    def test_synthetic_payload_excludes_local_name_maps(self):
        payload = json.dumps(get_data.build_model_payload(self.data))
        get_data.assert_no_real_names(payload, self.data)
        self.assertNotIn("agent_map", payload)
        self.assertIsNone(get_data.RE_EMAIL.search(payload))
        self.assertIsNone(get_data.RE_PHONE.search(payload))
        self.assertEqual(self.data["source_kind"], "synthetic")

    def test_record_ids_are_unique_and_traceable(self):
        ids = [r["record_id"] for r in self.data["records"]]
        self.assertEqual(len(ids), len(set(ids)))
        index = pipeline.index_records(self.data)
        self.assertIn(ids[0], index)


class PipelineParsingTests(unittest.TestCase):
    def test_json_is_extracted_from_fenced_text(self):
        text = 'Here you go:\n```json\n{"summary": "x", "findings": []}\n```\nThanks'
        self.assertEqual(pipeline.extract_json(text), {"summary": "x", "findings": []})

    def test_json_extraction_failure_is_clear(self):
        with self.assertRaises(pipeline.PipelineError):
            pipeline.extract_json("no json here")

    def test_citation_validation_flags_unknown_records(self):
        fake_data = {
            "records": [
                {
                    "record_id": "ME-001",
                    "behavior": "Convey Confidence",
                    "agent": "Agent-01",
                    "comment": "did well",
                    "result": "Yes",
                    "call_date": "2026-08-02",
                    "source": "ME",
                }
            ],
            "behaviors": [
                {
                    "id": "B01",
                    "behavior": "Convey Confidence",
                    "source": "ME",
                    "section": "Member Experience",
                    "evaluated": 1,
                    "failed": 0,
                    "defect_rate_pct": 0,
                    "pattern": "no failures",
                    "agents_failed": [],
                    "n_agents_failed": 0,
                    "evidence": [],
                }
            ],
        }
        diagnosis = {
            "findings": [
                {
                    "id": "F1",
                    "behavior_id": "B01",
                    "evidence": [
                        {"record_id": "ME-001", "quote": "did well"},
                        {"record_id": "ME-999", "quote": "made up"},
                    ],
                }
            ]
        }
        pipeline.validate_diagnosis(diagnosis, fake_data)
        ev_ = diagnosis["findings"][0]["evidence"]
        self.assertTrue(ev_[0]["verified"])
        self.assertFalse(ev_[1]["verified"])
        self.assertEqual(
            diagnosis["findings"][0]["defect_rate_pct"], 0
        )  # numbers come from Python, not the model

    def test_module_citations_are_verified(self):
        """Stage 4 must verify record IDs the same way Stage 2 does: invented IDs are caught, and
        real IDs that belong to a DIFFERENT behavior are flagged rather than silently accepted."""
        fake_data = {
            "records": [
                {
                    "record_id": "ME-001",
                    "behavior": "Convey Confidence",
                    "agent": "Agent-01",
                    "comment": "did not sound sure",
                    "result": "No",
                    "call_date": "2026-08-02",
                    "source": "ME",
                },
                {
                    "record_id": "BP-001",
                    "behavior": "Accurate Documentation Value",
                    "agent": "Agent-02",
                    "comment": "missing claim number",
                    "result": "No",
                    "call_date": "2026-08-03",
                    "source": "BP",
                },
            ]
        }
        finding = {"id": "F1", "behavior": "Convey Confidence"}
        module = {
            "module_title": "t",
            "learning_objective": "o",
            "delivery": "coaching",
            "why_this_fixes_the_gap": "Evidence from ME-001, BP-001 and ME-777 supports this.",
            "instruction": [],
            "roleplay_script": [],
            "knowledge_check": {},
            "hands_on_exercise": {},
            "simulation_scenario": {},
        }
        m = pipeline.validate_module(module, finding, fake_data)
        self.assertEqual(m["citations_total"], 3)
        self.assertEqual(
            m["citations_verified"], 1
        )  # wrong-behavior citations are not verified
        self.assertEqual(
            m["citations_off_behavior"], ["BP-001"]
        )  # real, but wrong behavior
        invented = [c for c in m["citations"] if c["record_id"] == "ME-777"][0]
        self.assertFalse(invented["exists"])

    def test_module_validation_without_data_still_works(self):
        """validate_module(data=None) must not crash - citation checking is optional."""
        m = pipeline.validate_module(
            {"module_title": "t"}, {"id": "F1", "behavior": "x"}
        )
        self.assertEqual(m["finding_id"], "F1")
        self.assertNotIn("citations", m)

    def test_quote_or_behavior_mismatch_is_not_verified(self):
        data = {
            "records": [
                {
                    "record_id": "BP-001",
                    "behavior": "Document next steps",
                    "source": "BP",
                    "agent": "Agent-01",
                    "call_date": "2026-09-01",
                    "result": "No",
                    "comment": "The follow-up owner was missing.",
                },
                {
                    "record_id": "CO-001",
                    "behavior": "Verify identity",
                    "source": "CO",
                    "agent": "Agent-01",
                    "call_date": "2026-09-01",
                    "result": "No",
                    "comment": "The agent skipped verification.",
                },
            ],
            "behaviors": [
                {
                    "id": "B01",
                    "behavior": "Document next steps",
                    "source": "BP",
                    "section": "Business Process",
                    "evaluated": 1,
                    "failed": 1,
                    "defect_rate_pct": 100,
                    "agents_failed": ["Agent-01"],
                    "pattern": "isolated",
                }
            ],
        }
        diagnosis = {
            "findings": [
                {
                    "behavior_id": "B01",
                    "evidence": [
                        {"record_id": "BP-001", "quote": "This quote was invented"},
                        {
                            "record_id": "CO-001",
                            "quote": "The agent skipped verification.",
                        },
                        {"record_id": "BP-001", "quote": "follow-up owner was missing"},
                    ],
                }
            ]
        }
        f = pipeline.validate_diagnosis(diagnosis, data)["findings"][0]
        self.assertEqual([e["verified"] for e in f["evidence"]], [False, False, True])
        self.assertEqual(f["evidence_verified"], 1)

    def test_private_cloud_upload_requires_explicit_approval(self):
        with self.assertRaises(pipeline.PipelineError):
            pipeline.ensure_cloud_upload_authorized(
                {"source_kind": "private", "checks": []}
            )

    def test_unverified_cross_behavior_evidence_cannot_enter_training_prompt(self):
        data = get_data.get_all_data(source_override="synthetic")
        finding = sample_walkthrough.demo_diagnosis(data)["findings"][0]
        wrong = next(r for r in data["records"] if r["source"] != finding["source"])
        finding["evidence"].insert(
            0, {"record_id": wrong["record_id"], "quote": wrong["comment"], "verified": False}
        )
        lines = pipeline.evidence_lines_for(finding, data)
        self.assertNotIn(wrong["record_id"], lines)
        self.assertIn("CO-", lines)

    def test_missing_training_fields_are_rejected_before_generation_succeeds(self):
        with self.assertRaises(pipeline.PipelineError):
            pipeline.ensure_module_complete(
                pipeline.validate_module({}, {"id": "F1", "behavior": "Verify identity"})
            )

    def test_synthetic_modules_use_distinct_behavior_matched_quizzes(self):
        data = get_data.get_all_data(source_override="synthetic")
        diagnosis = sample_walkthrough.demo_diagnosis(data)
        approved = pipeline.build_approved_findings(diagnosis, {})
        result = sample_walkthrough.demo_training(approved, data)
        modules = {m["finding_id"]: m for m in result["modules"]}
        self.assertNotEqual(
            modules["F1"]["knowledge_check"]["question"],
            modules["F2"]["knowledge_check"]["question"],
        )
        self.assertTrue(all(r["quiz_matches_behavior"] for r in result["alignment"]))

    def test_synthetic_data_override_ignores_private_env_setting(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"QA_DATA_SOURCE": "private", "ALLOW_PRIVATE_QA_READ": "1"}):
            data = get_data.get_all_data(source_override="synthetic")
        self.assertEqual(data["source_kind"], "synthetic")

    def test_alignment_check(self):
        approved = [
            {
                "id": "F1",
                "behavior": "Displayed Active Listening Skills and Responsiveness",
            }
        ]
        modules = [
            {
                "finding_id": "F1",
                "simulation_scenario": {
                    "behavioral_target": "Active listening and responsiveness"
                },
            },
            {
                "finding_id": "F7",
                "simulation_scenario": {"behavioral_target": "Something else"},
            },
        ]
        rows = pipeline.alignment_check(approved, modules)
        self.assertTrue(
            rows[0]["finding_exists"] and rows[0]["target_matches_behavior"]
        )
        self.assertFalse(rows[1]["finding_exists"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

