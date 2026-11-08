"""Illustrative AWS AgentCore invocation, NOT original competition source.

Requires boto3, an authorized runtime, and AWS credentials obtained by the
SDK credential chain. Importing this module does not contact AWS.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4


def invoke_sanitized_prompt(prompt: str) -> str:
    """Invoke an approved runtime with a synthetic or reviewed prompt.

    Do not use this with customer information or private competition data.
    The payload schema must match the actual deployed runtime's contract.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A nonempty sanitized prompt is required")

    runtime_arn = os.getenv("HARNESS_ARN") or os.getenv("AGENT_RUNTIME_ARN")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    if not runtime_arn:
        raise RuntimeError("Set HARNESS_ARN or AGENT_RUNTIME_ARN securely")
    if not region:
        raise RuntimeError("Set AWS_REGION or AWS_DEFAULT_REGION")

    # Deliberately import only when invoked so offline validation is AWS-free.
    import boto3

    client = boto3.client("bedrock-agentcore", region_name=region)
    session_id = str(uuid4())  # A 36-character string; UUID objects are invalid.
    payload = json.dumps({"prompt": prompt}).encode("utf-8")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        contentType="application/json",
        payload=payload,
    )

    body = response["response"]
    try:
        return body.read().decode("utf-8")
    finally:
        body.close()

