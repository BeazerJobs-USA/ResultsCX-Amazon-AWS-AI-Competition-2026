"""AWS Bedrock AgentCore InvokeHarness client and event-stream parsing.

This module owns cloud calls. Credentials are resolved by Boto3's provider
chain; no access keys are passed as application parameters.
"""

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import botocore.exceptions
from dotenv import load_dotenv

# Load .env that sits next to this file (works no matter where the app is launched from).
load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger("harness")

HARNESS_ARN_PATTERN = re.compile(
    r"^arn:([^:]+)?:bedrock-agentcore:([a-z0-9-]+):[0-9]{12}:harness/[A-Za-z][A-Za-z0-9_]{0,39}-[A-Za-z0-9]{10}$"
)

# Stop waiting for an idle AWS response after two minutes by default. This is
# a socket read timeout, not a guarantee on the total time for several modules.
DEFAULT_READ_TIMEOUT = 120


class HarnessError(Exception):
    """Raised for a failed or invalid Bedrock harness request."""


@dataclass
class ToolUse:
    tool_use_id: str
    name: str
    input_text: str = ""  # accumulated JSON fragments
    input: dict = field(default_factory=dict)


@dataclass
class HarnessTurn:
    """Everything one invoke_harness call returned."""

    text: str = ""
    tool_uses: list = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    unexpected_events: list = field(default_factory=list)


def new_session_id():
    """A fresh runtimeSessionId. Must be a string (33-100 chars); a UUID string is 36."""
    return str(uuid.uuid4())


def get_config():
    """Read and validate configuration. Never prints secret values."""
    arn = os.environ.get("HARNESS_ARN", "").strip()
    if not arn:
        raise HarnessError(
            "HARNESS_ARN is not configured.\n"
            "What failed: harness_client.get_config()\n"
            "Why: the app needs the ARN of the AgentCore harness to call Bedrock.\n"
            "Fix: create a file named .env next to app.py (copy .env.example) and add the line\n"
            "     HARNESS_ARN=arn:aws:bedrock-agentcore:us-east-1:<account-id>:harness/<name>"
        )
    m = HARNESS_ARN_PATTERN.match(arn)
    if not m:
        raise HarnessError(
            "HARNESS_ARN does not match the expected harness ARN format.\n"
            "Expected format: arn:aws:bedrock-agentcore:us-east-1:<12-digit-account>:harness/<name>-<10 chars>\n"
            "Fix: confirm the approved identifier and region with your AWS administrator."
        )
    region = os.environ.get("AWS_DEFAULT_REGION", "").strip() or m.group(2)
    max_tokens = os.environ.get("HARNESS_MAX_TOKENS", "").strip()
    if max_tokens and (not max_tokens.isdecimal() or int(max_tokens) <= 0):
        raise HarnessError("HARNESS_MAX_TOKENS must be a positive integer")
    timeout = os.environ.get("HARNESS_READ_TIMEOUT", str(DEFAULT_READ_TIMEOUT)).strip()
    if not timeout.isdecimal() or not 15 <= int(timeout) <= 600:
        raise HarnessError("HARNESS_READ_TIMEOUT must be 15 to 600 seconds")
    return {
        "harness_arn": arn,
        "region": region,
        "max_tokens": int(max_tokens) if max_tokens.isdigit() else None,
        "model_id": os.environ.get("BEDROCK_MODEL_ID", "").strip() or None,
        "read_timeout": int(timeout),
    }


def make_client(region, read_timeout=DEFAULT_READ_TIMEOUT):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:  # pragma: no cover
        raise HarnessError(
            "boto3 is not installed.\nFix: in the activated virtual environment run\n"
            "     pip install -r requirements.txt"
        ) from e
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            connect_timeout=15, read_timeout=read_timeout, retries={"max_attempts": 2}
        ),
    )


# ======================================================================================
# Stream parsing
# ======================================================================================
def parse_stream(stream, on_text=None):
    """Consume an InvokeHarness event stream and return a HarnessTurn.

    ``on_text(chunk)`` is called for every text fragment so a UI can show live progress.
    Never raises KeyError on a malformed event: unknown shapes are recorded in
    ``turn.unexpected_events`` and the stream continues.
    """
    turn = HarnessTurn()
    open_tools = {}  # contentBlockIndex -> ToolUse being assembled

    try:
        return _parse_stream_events(stream, turn, open_tools, on_text)
    except botocore.exceptions.EventStreamError as e:
        # Some error conditions (e.g. the harness's own max-token limit) are raised by botocore's
        # eventstream decoder itself, before our code ever sees a plain event dict - so the
        # (validationException / internalServerException / runtimeClientError) handling below never
        # runs for these. Catch them here and translate the same way instead of crashing with a
        # raw traceback.
        error = e.response.get("Error", {})
        code, msg = error.get("Code", "Unknown"), error.get("Message", "Unknown")
        hint = "Fix: retry once; if it persists, check the harness in the AWS console."
        if (
            "maximum token limit" in msg.lower()
            or "max_tokens" in msg.lower()
            or "maxtokens" in msg.lower()
        ):
            hint = (
                "Fix: the model ran out of room to finish its answer. Try, in order: (1) raise "
                "HARNESS_MAX_TOKENS in .env (e.g. to 16000) and retry, (2) if it still happens, lower "
                "the number of findings requested (python main.py --max-findings 3), which shortens "
                "the required answer."
            )
        raise HarnessError(
            f"Bedrock returned {code}.\n"
            "What failed: harness_client.parse_stream (the AWS event stream itself raised an error)\n"
            f"{hint}"
        ) from e
    except botocore.exceptions.ReadTimeoutError as e:
        raise HarnessError(
            "Bedrock stopped sending stream data before the read timeout. "
            "The request did not finish. Retry once or increase "
            "HARNESS_READ_TIMEOUT for an approved slow-running harness."
        ) from e


def _parse_stream_events(stream, turn, open_tools, on_text):
    """Does the actual event-by-event parsing; called by parse_stream() above so a mid-stream
    EventStreamError (raised by botocore's decoder, not delivered as a plain event) can be caught
    in one place. Returns the completed ``turn``."""
    for event in stream:
        if not isinstance(event, dict):
            turn.unexpected_events.append(f"non-dict event: {type(event).__name__}")
            continue

        # ---- error events: surface them, do not swallow ----
        for err_key in (
            "validationException",
            "internalServerException",
            "runtimeClientError",
        ):
            if err_key in event:
                detail = event[err_key] or {}
                msg = detail.get("message", "(no message)")
                extra = ""
                if detail.get("reason"):
                    extra += f" reason={detail['reason']}"
                if detail.get("fieldList"):
                    extra += " fields=" + "; ".join(
                        f"{f.get('name')}: {f.get('message')}"
                        for f in detail["fieldList"]
                    )
                raise HarnessError(
                    f"Bedrock returned {err_key}: {msg}{extra}\n"
                    "What failed: harness_client.parse_stream (the stream itself carried an error event)\n"
                    "Type: AWS/configuration problem if validationException, AWS service problem otherwise.\n"
                    "Fix: for validationException check the message format sent; for the others retry once, "
                    "then check the AWS console for the harness status."
                )

        if "messageStart" in event:
            continue

        if "contentBlockStart" in event:
            block = event["contentBlockStart"] or {}
            start = block.get("start") or {}
            idx = block.get("contentBlockIndex", len(open_tools))
            if "toolUse" in start:
                tu = start["toolUse"] or {}
                open_tools[idx] = ToolUse(
                    tool_use_id=tu.get("toolUseId", ""), name=tu.get("name", "")
                )
                log.info("model requested tool: %s", open_tools[idx].name)
            continue

        if "contentBlockDelta" in event:
            block = event["contentBlockDelta"] or {}
            delta = block.get("delta") or {}
            idx = block.get("contentBlockIndex")
            if "text" in delta:
                # An empty string ("") is a normal, harmless delta - just don't add noise to the
                # visible text or call on_text() for nothing.
                if delta["text"]:
                    turn.text += delta["text"]
                    if on_text:
                        on_text(delta["text"])
            elif "toolUse" in delta:
                fragment = (delta["toolUse"] or {}).get("input", "")
                if idx not in open_tools:  # start event missing: create a placeholder
                    open_tools[idx] = ToolUse(tool_use_id="", name="")
                open_tools[idx].input_text += fragment or ""
            elif "reasoningContent" in delta:
                pass  # model thinking - not needed in output
            else:
                turn.unexpected_events.append(
                    f"unrecognized contentBlockDelta keys: {list(delta)}"
                )
            continue

        if "contentBlockStop" in event:
            idx = (event["contentBlockStop"] or {}).get("contentBlockIndex")
            if idx in open_tools:
                tu = open_tools[idx]
                raw = tu.input_text.strip()
                try:
                    tu.input = json.loads(raw) if raw else {}
                except json.JSONDecodeError as e:
                    raise HarnessError(
                        f"Could not parse the tool input the model sent for '{tu.name}': {e}\n"
                        "What failed: harness_client.parse_stream -> json.loads\n"
                        "Type: model output problem. Fix: retry; if it persists, simplify the tool's input schema."
                    ) from e
                turn.tool_uses.append(tu)
            continue

        if "messageStop" in event:
            turn.stop_reason = (event["messageStop"] or {}).get("stopReason", "")
            continue

        if "metadata" in event:
            usage = (event["metadata"] or {}).get("usage") or {}
            turn.input_tokens += int(usage.get("inputTokens") or 0)
            turn.output_tokens += int(usage.get("outputTokens") or 0)
            metrics = (event["metadata"] or {}).get("metrics") or {}
            turn.latency_ms += int(metrics.get("latencyMs") or 0)
            continue

        turn.unexpected_events.append(f"unrecognized event keys: {list(event)}")

    # Tool blocks that never got a contentBlockStop (defensive)
    for tu in open_tools.values():
        if tu not in turn.tool_uses:
            raw = tu.input_text.strip()
            try:
                tu.input = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                tu.input = {}
            turn.tool_uses.append(tu)

    if turn.unexpected_events:
        log.warning("%d unexpected stream events", len(turn.unexpected_events))
    return turn


# ======================================================================================
# Conversation with automatic tool handling
# ======================================================================================
class HarnessSession:
    """One AgentCore session (one runtimeSessionId) reused across all pipeline stages.

    ``tools`` maps a tool name -> Python function returning a JSON-serialisable result. If the
    harness is configured with a tool the model decides to call (e.g. ``get_all_data``), we run it
    locally and send the result back in the same session - up to ``max_tool_rounds`` times.
    """

    def __init__(self, tools=None, client=None, config=None, max_tool_rounds=4):
        self.config = config or get_config()
        self.client = client or make_client(
            self.config["region"], self.config.get("read_timeout", DEFAULT_READ_TIMEOUT)
        )
        self.session_id = new_session_id()
        self.tools = tools or {}
        self.max_tool_rounds = max_tool_rounds
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls = 0

    def _invoke(self, messages, system_prompt, on_text):
        kwargs = {
            "harnessArn": self.config["harness_arn"],
            "runtimeSessionId": self.session_id,  # str, 36 chars - NOT a uuid.UUID object
            "messages": messages,
        }
        if system_prompt:
            kwargs["systemPrompt"] = [{"text": system_prompt}]
        if self.config.get("max_tokens"):
            kwargs["maxTokens"] = self.config["max_tokens"]
        if self.config.get("model_id"):
            kwargs["model"] = {
                "bedrockModelConfig": {
                    "modelId": self.config["model_id"],
                    "temperature": 0.0,
                }
            }
        try:
            response = self.client.invoke_harness(**kwargs)
        except (
            Exception
        ) as e:  # botocore raises many classes; translate the common ones
            name = type(e).__name__
            hint = (
                "Fix: retry once; if it persists, check the harness in the AWS console."
            )
            if "NoCredentials" in name or "PartialCredentials" in name:
                hint = "Fix: configure an approved AWS profile, IAM Identity Center, or short-lived role credentials."
            elif "ParamValidation" in name:
                hint = "Fix: a request field has the wrong type/shape - see message above (code problem in harness_client.py)."
            elif (
                "AccessDenied" in name
                or "UnrecognizedClient" in name
                or "InvalidSignature" in name
            ):
                hint = "Fix: check the active identity and its bedrock-agentcore:InvokeHarness permission."
            elif "ResourceNotFound" in name:
                hint = "Fix: HARNESS_ARN points to a harness that does not exist in this region/account."
            elif "EndpointConnectionError" in name or "ConnectTimeout" in name:
                hint = "Fix: no network path to AWS - check Wi-Fi/VPN, then use the offline replay mode for the demo."
            raise HarnessError(
                f"invoke_harness failed with {name}.\n"
                f"What failed: harness_client.HarnessSession._invoke\n{hint}"
            ) from e
        self.calls += 1
        if not isinstance(response, dict) or "stream" not in response:
            raise HarnessError(
                "The AWS response did not contain an event stream. Check the harness configuration."
            )
        turn = parse_stream(response["stream"], on_text=on_text)
        self.total_input_tokens += turn.input_tokens
        self.total_output_tokens += turn.output_tokens
        return turn

    def ask(self, user_text, system_prompt=None, on_text=None):
        """Send one user message and return the model's final text (after any tool rounds)."""
        messages = [{"role": "user", "content": [{"text": user_text}]}]
        final_text = ""
        last_turn = None
        for round_no in range(self.max_tool_rounds + 1):
            turn = self._invoke(messages, system_prompt, on_text)
            last_turn = turn
            if not turn.tool_uses:
                # Earlier tool rounds can include commentary or partial JSON.
                # Only the final model reply should be parsed as a diagnosis/module.
                final_text = turn.text
                break
            if round_no == self.max_tool_rounds:
                raise HarnessError(
                    f"The model kept calling tools ({self.max_tool_rounds} rounds) without finishing.\n"
                    "Fix: retry; if it persists, remove the tool from the harness configuration."
                )
            # Answer every tool call, then continue in the SAME session.
            assistant_blocks, result_blocks = [], []
            if turn.text.strip():
                assistant_blocks.append({"text": turn.text})
            for tu in turn.tool_uses:
                assistant_blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": tu.tool_use_id,
                            "name": tu.name,
                            "input": tu.input,
                        }
                    }
                )
                fn = self.tools.get(tu.name)
                if fn is None:
                    result_blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": tu.tool_use_id,
                                "status": "error",
                                "content": [
                                    {
                                        "text": f"Tool '{tu.name}' is not available in this application. "
                                        "All required data is already included in the user message - use it."
                                    }
                                ],
                            }
                        }
                    )
                    continue
                try:
                    result = fn(**tu.input) if isinstance(tu.input, dict) else fn()
                    result_blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": tu.tool_use_id,
                                "status": "success",
                                "content": [{"text": json.dumps(result, default=str)}],
                            }
                        }
                    )
                except Exception as e:
                    result_blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": tu.tool_use_id,
                                "status": "error",
                                "content": [
                                    {"text": f"Tool failed ({type(e).__name__}). Use the supplied QA summary."}
                                ],
                            }
                        }
                    )
            messages = [
                {"role": "assistant", "content": assistant_blocks},
                {"role": "user", "content": result_blocks},
            ]

        stop = last_turn.stop_reason if last_turn else ""
        if stop in ("max_tokens", "max_output_tokens_exceeded"):
            raise HarnessError(
                f"The model stopped early (stopReason={stop}) so its answer is cut off.\n"
                "Fix: add  HARNESS_MAX_TOKENS=8000  to .env (or raise it), then retry."
            )
        if stop in (
            "content_filtered",
            "malformed_model_output",
            "malformed_tool_use",
            "model_context_window_exceeded",
            "timeout_exceeded",
            "max_iterations_exceeded",
        ):
            raise HarnessError(
                f"The model did not finish normally (stopReason={stop}).\n"
                "Fix: retry once. If it repeats, shorten the input (fewer evidence comments) or check the harness settings."
            )
        if not final_text.strip():
            raise HarnessError(
                f"The model returned no text (stopReason={stop or 'unknown'}).\n"
                "What failed: harness_client.HarnessSession.ask\n"
                "Fix: retry; if it repeats, run  python main.py --debug  and send the event log to the team."
            )
        return final_text, last_turn
