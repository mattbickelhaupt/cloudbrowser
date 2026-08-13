"""
CloudBrowser Lambda Handler
Discovers AWS resources matching configured patterns, queries their CloudWatch logs
and metrics over the lookback window, feeds the data to a Bedrock model, and
publishes the resulting report to SNS.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Environment ───────────────────────────────────────────────────────────────

BEDROCK_MODEL_ID         = os.environ["BEDROCK_MODEL_ID"]
SNS_TOPIC_ARN            = os.environ["SNS_TOPIC_ARN"]
AWS_REGION_TARGET        = os.environ.get("AWS_REGION_TARGET", os.environ.get("AWS_REGION", "us-east-1"))
MODULE_NAME              = os.environ.get("MODULE_NAME", "cloudbrowser")
LAMBDA_PATTERNS          = json.loads(os.environ.get("LAMBDA_PATTERNS", "[]"))
ECS_CLUSTER_PATTERNS     = json.loads(os.environ.get("ECS_CLUSTER_PATTERNS", "[]"))
STEP_FUNCTION_PATTERNS   = json.loads(os.environ.get("STEP_FUNCTION_PATTERNS", "[]"))
LOG_GROUP_PATTERNS       = json.loads(os.environ.get("LOG_GROUP_PATTERNS", "[]"))
LOG_LEVELS               = json.loads(os.environ.get("LOG_LEVELS", '["ERROR","WARN"]'))
LOOKBACK_HOURS           = int(os.environ.get("LOOKBACK_HOURS", "24"))
MAX_LOG_EVENTS_PER_GROUP = int(os.environ.get("MAX_LOG_EVENTS_PER_GROUP", "500"))

# ── AWS Clients ───────────────────────────────────────────────────────────────

session     = boto3.session.Session(region_name=AWS_REGION_TARGET)
lambda_cl   = session.client("lambda")
ecs_cl      = session.client("ecs")
sfn_cl      = session.client("stepfunctions")
logs_cl     = session.client("logs")
cw_cl       = session.client("cloudwatch")
bedrock_cl  = session.client("bedrock-runtime")
sns_cl      = session.client("sns")


# ═══════════════════════════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════════════════════════

def matches_any(name: str, patterns: list[str]) -> bool:
    """Return True if *name* matches any of the shell-style glob *patterns*."""
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def time_range() -> tuple[int, int]:
    """Return (start_ms, end_ms) for the configured lookback window."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=LOOKBACK_HOURS)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def paginate(client_method, result_key: str, **kwargs) -> list:
    """Generic paginator that collects all items from a paginated AWS API."""
    items: list = []
    paginator = client_method(**kwargs)
    for page in paginator:
        items.extend(page.get(result_key, []))
    return items


# ═══════════════════════════════════════════════════════════════════════════════
# Resource Discovery
# ═══════════════════════════════════════════════════════════════════════════════

def discover_lambda_log_groups() -> list[str]:
    if not LAMBDA_PATTERNS:
        return []

    logger.info("Discovering Lambda functions matching patterns: %s", LAMBDA_PATTERNS)
    paginator = lambda_cl.get_paginator("list_functions")
    log_groups: list[str] = []

    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            name = fn["FunctionName"]
            if matches_any(name, LAMBDA_PATTERNS):
                log_groups.append(f"/aws/lambda/{name}")

    logger.info("Found %d Lambda log group(s)", len(log_groups))
    return log_groups


def discover_ecs_log_groups() -> list[str]:
    if not ECS_CLUSTER_PATTERNS:
        return []

    logger.info("Discovering ECS clusters matching patterns: %s", ECS_CLUSTER_PATTERNS)
    cluster_arns = paginate(
        ecs_cl.get_paginator("list_clusters").paginate,
        "clusterArns",
    )

    matched_clusters: list[str] = []
    for arn in cluster_arns:
        cluster_name = arn.split("/")[-1]
        if matches_any(cluster_name, ECS_CLUSTER_PATTERNS):
            matched_clusters.append(arn)

    log_groups: list[str] = []
    for cluster_arn in matched_clusters:
        cluster_name = cluster_arn.split("/")[-1]
        log_groups.append(f"/ecs/{cluster_name}")

        service_arns = paginate(
            ecs_cl.get_paginator("list_services").paginate,
            "serviceArns",
            cluster=cluster_arn,
        )
        for svc_arn in service_arns:
            svc_name = svc_arn.split("/")[-1]
            log_groups.append(f"/ecs/{cluster_name}/{svc_name}")

    logger.info("Found %d ECS log group candidate(s)", len(log_groups))
    return log_groups


def discover_sfn_log_groups() -> list[str]:
    if not STEP_FUNCTION_PATTERNS:
        return []

    logger.info("Discovering Step Functions matching patterns: %s", STEP_FUNCTION_PATTERNS)
    machines = paginate(
        sfn_cl.get_paginator("list_state_machines").paginate,
        "stateMachines",
    )

    log_groups: list[str] = []
    for sm in machines:
        name = sm["name"]
        if matches_any(name, STEP_FUNCTION_PATTERNS):
            log_groups.append(f"/aws/states/{name}")

    logger.info("Found %d Step Function log group candidate(s)", len(log_groups))
    return log_groups


def discover_explicit_log_groups() -> list[str]:
    """Resolve user-supplied log group patterns via prefix search."""
    if not LOG_GROUP_PATTERNS:
        return []

    logger.info("Discovering explicit log groups matching patterns: %s", LOG_GROUP_PATTERNS)
    found: list[str] = []
    for pattern in LOG_GROUP_PATTERNS:
        prefix = pattern.rstrip("*")
        paginator = logs_cl.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            for lg in page.get("logGroups", []):
                name = lg["logGroupName"]
                if fnmatch.fnmatch(name, pattern):
                    found.append(name)

    logger.info("Found %d explicit log group(s)", len(found))
    return found


def resolve_log_groups() -> list[str]:
    """Collect all log groups from every discovery source, deduplicated."""
    candidates = (
        discover_lambda_log_groups()
        + discover_ecs_log_groups()
        + discover_sfn_log_groups()
        + discover_explicit_log_groups()
    )

    # Verify each group actually exists in CloudWatch
    existing: set[str] = set()
    for name in candidates:
        try:
            resp = logs_cl.describe_log_groups(logGroupNamePrefix=name, limit=1)
            for lg in resp.get("logGroups", []):
                if lg["logGroupName"] == name:
                    existing.add(name)
        except Exception as exc:
            logger.warning("Could not verify log group %s: %s", name, exc)

    return sorted(existing)


# ═══════════════════════════════════════════════════════════════════════════════
# Log Retrieval
# ═══════════════════════════════════════════════════════════════════════════════

def build_filter_pattern() -> str:
    """Build a CloudWatch filter pattern string from the configured log levels."""
    if "ALL" in LOG_LEVELS:
        return ""  # empty pattern = match everything

    terms = [f'"{lvl}"' for lvl in LOG_LEVELS]
    return "?" + " ?".join(terms) if len(terms) > 1 else terms[0]


def fetch_log_events(log_group: str, start_ms: int, end_ms: int) -> list[dict]:
    """Retrieve up to MAX_LOG_EVENTS_PER_GROUP events from a log group."""
    filter_pattern = build_filter_pattern()
    events: list[dict] = []
    kwargs: dict[str, Any] = {
        "logGroupName": log_group,
        "startTime":    start_ms,
        "endTime":      end_ms,
        "limit":        min(MAX_LOG_EVENTS_PER_GROUP, 10000),
    }
    if filter_pattern:
        kwargs["filterPattern"] = filter_pattern

    try:
        paginator = logs_cl.get_paginator("filter_log_events")
        for page in paginator.paginate(**kwargs):
            for evt in page.get("events", []):
                events.append({
                    "timestamp": datetime.fromtimestamp(
                        evt["timestamp"] / 1000, tz=timezone.utc
                    ).isoformat(),
                    "message": evt["message"].strip(),
                })
                if len(events) >= MAX_LOG_EVENTS_PER_GROUP:
                    return events
    except Exception as exc:
        logger.warning("Error fetching logs for %s: %s", log_group, exc)

    return events


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics Retrieval
# ═══════════════════════════════════════════════════════════════════════════════

def _get_metric(namespace: str, metric_name: str, dimensions: list[dict],
                start_ms: int, end_ms: int, stat: str = "Sum") -> list[dict]:
    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt   = datetime.fromtimestamp(end_ms   / 1000, tz=timezone.utc)
    period   = max(3600, int((end_ms - start_ms) / 1000 / 24))  # ~hourly buckets

    try:
        resp = cw_cl.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_dt,
            EndTime=end_dt,
            Period=period,
            Statistics=[stat],
        )
        return sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
    except Exception as exc:
        logger.warning("Metric fetch failed (%s/%s): %s", namespace, metric_name, exc)
        return []


def fetch_lambda_metrics(function_name: str, start_ms: int, end_ms: int) -> dict:
    dims = [{"Name": "FunctionName", "Value": function_name}]
    return {
        "invocations": sum(d["Sum"] for d in _get_metric(
            "AWS/Lambda", "Invocations", dims, start_ms, end_ms)),
        "errors": sum(d["Sum"] for d in _get_metric(
            "AWS/Lambda", "Errors", dims, start_ms, end_ms)),
        "throttles": sum(d["Sum"] for d in _get_metric(
            "AWS/Lambda", "Throttles", dims, start_ms, end_ms)),
        "duration_avg_ms": (
            lambda pts: (sum(d["Average"] for d in pts) / len(pts)) if pts else 0
        )(_get_metric("AWS/Lambda", "Duration", dims, start_ms, end_ms, stat="Average")),
    }


def collect_metrics_summary(log_groups: list[str], start_ms: int, end_ms: int) -> dict:
    summary: dict[str, dict] = {}
    for lg in log_groups:
        if lg.startswith("/aws/lambda/"):
            fn_name = lg.removeprefix("/aws/lambda/")
            summary[lg] = fetch_lambda_metrics(fn_name, start_ms, end_ms)
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# Bedrock Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def build_prompt(log_data: dict[str, list[dict]], metrics: dict, run_meta: dict) -> str:
    lines: list[str] = [
        "You are a senior cloud-reliability engineer. Analyse the following AWS CloudWatch "
        "logs and metrics collected during the observation window and produce a report.",
        "",
        f"Observation window : {run_meta['start']} → {run_meta['end']}",
        f"Log levels scanned : {', '.join(LOG_LEVELS)}",
        f"Total log groups   : {len(log_data)}",
        "",
        "## Metrics Summary",
    ]

    if metrics:
        for lg, m in metrics.items():
            lines.append(f"\n### {lg}")
            for k, v in m.items():
                lines.append(f"  {k}: {v}")
    else:
        lines.append("No metric data collected.")

    lines += ["", "## Log Samples (newest last within each group)"]

    for lg, events in log_data.items():
        lines.append(f"\n### {lg}  ({len(events)} events)")
        for evt in events[-200:]:  # cap per-group to avoid prompt overflow
            lines.append(f"  [{evt['timestamp']}] {evt['message']}")

    lines += [
        "",
        "## Instructions",
        "Produce a report in the following exact JSON structure (no additional text outside the JSON):",
        "",
        '{"human_report": "<markdown formatted summary with headings, bullet points, key findings, '
        'error analysis, recommendations>", '
        '"machine_report": {"generated_at": "<ISO-8601>", "observation_window_hours": <number>, '
        '"log_groups_scanned": <number>, "total_events_analysed": <number>, '
        '"error_count": <number>, "warn_count": <number>, '
        '"top_errors": [{"message": "...", "count": <n>, "log_group": "..."}], '
        '"recommendations": [{"priority": "HIGH|MEDIUM|LOW", "description": "..."}], '
        '"health_score": <0-100>}}',
    ]

    return "\n".join(lines)


def invoke_bedrock(prompt: str) -> dict:
    """Call the configured Bedrock model and return the parsed JSON report."""
    model_id = BEDROCK_MODEL_ID

    # Support Anthropic Claude (Messages API) and Amazon Titan / other models
    if "anthropic" in model_id:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    elif "amazon.titan" in model_id:
        body = {
            "inputText": prompt,
            "textGenerationConfig": {"maxTokenCount": 4096, "temperature": 0.2},
        }
    elif "meta.llama" in model_id:
        body = {
            "prompt": f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n{prompt}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n",
            "max_gen_len": 4096,
            "temperature": 0.2,
        }
    else:
        # Generic fallback — works for many models
        body = {"inputText": prompt, "maxTokens": 4096}

    logger.info("Invoking Bedrock model: %s", model_id)
    response = bedrock_cl.invoke_model(
        modelId=model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    raw = json.loads(response["body"].read())

    # Extract text from model-specific response shapes
    if "anthropic" in model_id:
        text = raw["content"][0]["text"]
    elif "amazon.titan" in model_id:
        text = raw["results"][0]["outputText"]
    elif "meta.llama" in model_id:
        text = raw.get("generation", "")
    else:
        text = str(raw)

    # Extract JSON block from the response
    json_start = text.find("{")
    json_end   = text.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        try:
            return json.loads(text[json_start:json_end])
        except json.JSONDecodeError:
            pass

    # Fallback if JSON extraction fails
    return {
        "human_report": text,
        "machine_report": {"raw": True, "generated_at": datetime.now(timezone.utc).isoformat()},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Report Publishing
# ═══════════════════════════════════════════════════════════════════════════════

def publish_report(report: dict, run_meta: dict) -> None:
    human   = report.get("human_report", "No human report generated.")
    machine = report.get("machine_report", {})

    health = machine.get("health_score", "N/A")
    subject = (
        f"[CloudBrowser] {MODULE_NAME} | "
        f"Health {health}/100 | "
        f"{run_meta['start'][:10]}"
    )

    body = "\n".join([
        f"CloudBrowser Report — {MODULE_NAME}",
        f"Generated : {run_meta['end']}",
        f"Window    : {run_meta['start']} → {run_meta['end']}",
        "=" * 60,
        "",
        "HUMAN SUMMARY",
        "-" * 60,
        human,
        "",
        "MACHINE-READABLE DATA",
        "-" * 60,
        json.dumps(machine, indent=2, default=str),
    ])

    logger.info("Publishing report to SNS topic: %s", SNS_TOPIC_ARN)
    sns_cl.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=subject[:100],  # SNS subject limit
        Message=body,
        MessageAttributes={
            "module_name":   {"DataType": "String", "StringValue": MODULE_NAME},
            "health_score":  {"DataType": "String", "StringValue": str(health)},
            "report_format": {"DataType": "String", "StringValue": "text/plain+json"},
        },
    )
    logger.info("Report published successfully.")


# ═══════════════════════════════════════════════════════════════════════════════
# Lambda Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def lambda_handler(event: dict, context: Any) -> dict:
    start_time = time.time()
    start_ms, end_ms = time_range()

    run_meta = {
        "start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
        "end":   datetime.fromtimestamp(end_ms   / 1000, tz=timezone.utc).isoformat(),
    }

    logger.info("CloudBrowser run started | module=%s | window=%s → %s",
                MODULE_NAME, run_meta["start"], run_meta["end"])

    # 1. Discover log groups
    log_groups = resolve_log_groups()
    if not log_groups:
        msg = "No matching log groups found. Verify your pattern configuration."
        logger.warning(msg)
        sns_cl.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[CloudBrowser] {MODULE_NAME} — No log groups found",
            Message=msg,
        )
        return {"statusCode": 200, "body": msg}

    logger.info("Scanning %d log group(s): %s", len(log_groups), log_groups)

    # 2. Fetch logs
    log_data: dict[str, list[dict]] = {}
    for lg in log_groups:
        events = fetch_log_events(lg, start_ms, end_ms)
        log_data[lg] = events
        logger.info("  %s → %d event(s)", lg, len(events))

    total_events = sum(len(v) for v in log_data.values())
    logger.info("Total events collected: %d", total_events)

    # 3. Collect metrics
    metrics = collect_metrics_summary(log_groups, start_ms, end_ms)

    # 4. Build prompt and invoke Bedrock
    prompt = build_prompt(log_data, metrics, run_meta)
    report = invoke_bedrock(prompt)

    # Augment machine report with run metadata
    mr = report.get("machine_report", {})
    mr.update({
        "observation_window_start": run_meta["start"],
        "observation_window_end":   run_meta["end"],
        "log_groups_scanned":       len(log_groups),
        "total_events_analysed":    total_events,
        "elapsed_seconds":          round(time.time() - start_time, 1),
    })
    report["machine_report"] = mr

    # 5. Publish report via SNS
    publish_report(report, run_meta)

    return {
        "statusCode": 200,
        "body": {
            "log_groups_scanned": len(log_groups),
            "total_events":       total_events,
            "health_score":       mr.get("health_score"),
            "elapsed_seconds":    mr["elapsed_seconds"],
        },
    }
