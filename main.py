"""Terminal checks for the QA coaching prototype.

By default I can inspect synthetic data without AWS access. An explicit
--live flag is required to invoke the configured Bedrock harness.
"""

import argparse
import logging
import sys

import get_data
import pipeline
import prompts
from harness_client import HarnessError, HarnessSession, get_config


def check_config():
    """Validate configuration with STS without printing identities or ARNs."""
    print("1. Loading .env and checking HARNESS_ARN ...")
    cfg = get_config()
    print("   OK  HARNESS_ARN format validated")
    print(f"   OK  region      = {cfg['region']}")
    print(
        f"   --  HARNESS_MAX_TOKENS = {cfg['max_tokens'] or '(not set - harness default)'}"
    )
    print(
        "2. Checking AWS credentials with sts:GetCallerIdentity (free, no tokens used) ..."
    )
    import boto3

    try:
        ident = boto3.client("sts", region_name=cfg["region"]).get_caller_identity()
    except Exception as e:
        name = type(e).__name__
        print(f"   FAIL {name}")
        if any(w in name for w in ("Connection", "Endpoint", "Proxy", "Timeout")):
            print(
                "   Fix: this is a NETWORK problem, not a key problem - check Wi-Fi/VPN/proxy and retry."
            )
        else:
            print("   Fix: check your approved AWS profile or IAM role permissions.")
        return False
    account = ident.get("Account")
    print("   OK  current AWS identity verified")
    arn_account = cfg["harness_arn"].split(":")[4]
    if account != arn_account:
        print(
            "   WARN the runtime and current identity use different AWS accounts; cross-account access may be required."
        )
    print("3. Checking the data folder ...")
    data = get_data.get_all_data()
    t = data["totals"]
    print(
        f"   OK  {t['line_items']} line items, {t['calls_evaluated']} agent-date groups, {t['agents']} agents"
    )
    print("\nPreflight passed. Next:  python main.py")
    return True


def dry_run():
    data = get_data.get_all_data()
    payload = pipeline.build_safe_model_payload(data)
    prompt = prompts.build_diagnosis_prompt(payload)
    get_data.assert_no_real_names(prompt, data)
    print(
        f"Diagnosis prompt: {len(prompt):,} characters (~{len(prompt) // 4:,} tokens)."
    )
    print("Known employee-name check passed; this is not a complete privacy review.")


def print_diagnosis(diag):
    print("\n" + "=" * 78 + "\nDIAGNOSIS (Stage 2)\n" + "=" * 78)
    print(diag.get("summary", ""))
    for f in diag["findings"]:
        print(f"\n[{f['id']}] {f['title']}")
        print(
            f"     behavior: {f.get('behavior')}  ({f.get('section')})  "
            f"{f.get('defect_rate_pct', '?')}% = {f.get('failed', '?')}/{f.get('evaluated', '?')}  {f.get('computed_pattern', '')}"
        )
        print(f"     root cause: {f['root_cause_type']} - {f['root_cause']}")
        print(
            f"     coverage today: {f['curriculum_coverage']} - {f['curriculum_note']}"
        )
        print(
            f"     training recommended: {f['training_recommended']} -> {f['recommended_intervention']}  "
            f"(confidence {f['confidence']})"
        )
        print(
            f"     evidence: {f['evidence_verified']}/{f['evidence_total']} citations verified against the records"
        )
        for e in f["evidence"]:
            flag = "OK " if e["verified"] else "?? "
            print(f'       {flag}{e["record_id"]}: "{e["quote"][:90]}"')
    if diag.get("not_a_training_problem"):
        print("\nNOT a training problem:")
        for x in diag["not_a_training_problem"]:
            print(f"  - {x}")
    if diag.get("open_questions"):
        print("\nOpen questions for the supervisor:")
        for x in diag["open_questions"]:
            print(f"  - {x}")
    m = diag.get("_meta", {})
    print(
        f"\ntokens in/out: {m.get('input_tokens')}/{m.get('output_tokens')}  latency: {m.get('latency_ms')} ms  "
        f"saved to outputs/latest_diagnosis.json"
    )


def print_training(result):
    print("\n" + "=" * 78 + "\nTRAINING (Stage 4)\n" + "=" * 78)
    for mod in result["modules"]:
        print(
            f"\n[{mod['finding_id']}] {mod['module_title']}  ({mod['delivery']}, {mod.get('duration_minutes', '?')} min)"
        )
        print(f"  objective: {mod['learning_objective']}")
        print(f"  why: {mod['why_this_fixes_the_gap']}")
        sc = mod["simulation_scenario"]
        print(
            f"  scenario {sc.get('scenario_id', '')}: {sc.get('call_driver', '')} | member: {sc.get('member_emotion', '')}"
        )
        print(f"  behavioral target: {sc.get('behavioral_target', '')}")
        print(f"  role-play turns: {len(mod['roleplay_script'])}")
        total = mod.get("citations_total", 0)
        if total:
            bad = [c["record_id"] for c in mod.get("citations", []) if not c["exists"]]
            off = mod.get("citations_off_behavior", [])
            line = (
                f"  citations: {mod['citations_verified']}/{total} record IDs verified"
            )
            if bad:
                line += f"  INVENTED: {', '.join(bad)}"
            if off:
                line += f"  (about a different behavior: {', '.join(off)})"
            print(line)
    for err in result["errors"]:
        print(f"\n[{err['finding_id']}] FAILED: {err['error']}")
    print("\nAlignment check (Stage 5):")
    for row in result["alignment"]:
        ok = (
            row["finding_exists"]
            and row["target_matches_behavior"]
            and row["has_roleplay"]
            and row["has_scoring"]
        )
        print(
            f"  {'PASS' if ok else 'CHECK'}  {row['finding_id']} -> {row['module_title'][:60]}  shared terms: {row['shared_terms']}"
        )
    print("saved to outputs/latest_training.json")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="validate .env, credentials and data (no tokens used)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="build prompts offline, no AWS call"
    )
    ap.add_argument(
        "--live", action="store_true", help="explicitly allow a Bedrock invocation"
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="after diagnosis, auto-approve and generate training",
    )
    ap.add_argument("--debug", action="store_true", help="verbose logging")
    ap.add_argument("--max-findings", type=int, default=5)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.check:
            sys.exit(0 if check_config() else 1)
        if args.dry_run:
            dry_run()
            return
        if not args.live:
            dry_run()
            print(
                "No AWS call made. Use --live only with an authorized runtime and data source."
            )
            return

        print("Stage 1: loading and masking QA data ...")
        data = get_data.get_all_data()
        print(
            f"   {data['totals']['line_items']} line items, {data['totals']['failed_items']} failures, "
            f"masked: {data['scrub_log']}"
        )

        session = HarnessSession(
            tools={"get_all_data": lambda **kw: pipeline.build_safe_model_payload(data)}
        )
        print(
            f"Stage 2: asking Bedrock for the needs analysis (session {session.session_id[:8]}...) ..."
        )
        received = [0]

        def on_text(chunk):
            received[0] += len(chunk)
            print(f"\r   receiving ... {received[0]:,} characters", end="", flush=True)

        diagnosis = pipeline.run_diagnosis(
            session, data, max_findings=args.max_findings, on_text=on_text
        )
        print()
        print_diagnosis(diagnosis)

        if args.full:
            print(
                "\nStage 3: (auto-approving the findings marked training_recommended - TEST MODE ONLY)"
            )
            approved = pipeline.build_approved_findings(
                diagnosis, {}, approval_mode="test_auto_selection"
            )
            print(f"Stage 4: generating {len(approved)} training module(s) ...")
            received[0] = 0
            result = pipeline.run_training(
                session,
                approved,
                data,
                on_progress=lambda i, n, f: print(f"\n   module {i}/{n}: {f['title']}"),
                on_text=on_text,
            )
            print()
            print_training(result)
        print(
            f"\nSession tokens: {session.total_input_tokens:,} in / {session.total_output_tokens:,} out "
            f"over {session.calls} call(s)"
        )
    except (HarnessError, pipeline.PipelineError, get_data.DataError) as e:
        print(f"\nERROR ({type(e).__name__})\n{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
