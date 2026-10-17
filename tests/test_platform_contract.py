"""Trusted behavior checks copied into generated independent projects."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from role import execute


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = yaml.safe_load((ROOT / "agent.yaml").read_text(encoding="utf-8"))
TOOLS = {item["name"] for item in MANIFEST["spec"].get("tools", [])}
DELEGATES = MANIFEST["spec"].get("delegates") or []


def _normal_input():
    for line in (ROOT / "evals/seed.jsonl").read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case.get("expected", {}).get("decision") == "PASS":
            return case["input"]
    pytest.skip("No approved PASS case for the platform contract check")


class Prompt:
    def compile(self, **values):
        return json.dumps(values, ensure_ascii=False, default=str)


class Tools:
    async def call(self, name, **kwargs):
        values = {
            "ci.log.fetch": {"log": "CI completed successfully; tests passed", "logsUnavailable": False},
            "git.pr.diff": {"diff": "diff --git a/role.py b/role.py\n+validated", "number": kwargs.get("number")},
            "devflow.job.read": [{"id": kwargs.get("jobId"), "jobId": kwargs.get("jobId"), "status": "DONE"}],
            "devflow.batch.read": [{"batchId": kwargs.get("batchId"), "status": "DONE"}],
            "devflow.catalog.search": [{"name": "read-only-tool", "access": "read"}],
            "keel.gate.report.read": [{"jobId": kwargs.get("jobId"), "ref": "artifact", "sha256": "test"}],
            "shift.policy": {"category": kwargs.get("category"), "guidance": "review"},
        }
        if name not in TOOLS:
            raise AssertionError(f"Unapproved tool call: {name}")
        return values[name]


class Llm:
    def __init__(self, response):
        self.response = response

    async def chat(self, *args, **kwargs):
        return self.response


class Context:
    def __init__(self, response, child_result=None):
        self.tools = Tools()
        self.llm = Llm(response)
        self.manifest = SimpleNamespace(spec=SimpleNamespace(delegates=DELEGATES))
        self.child_result = child_result

    async def prompt(self, name):
        assert name == "role"
        return Prompt()

    async def delegate(self, name, text):
        assert name in DELEGATES
        assert text
        return {"agent": name, "result": self.child_result or "Evidence reviewed", "trace_id": "test"}


def test_model_blocked_remains_blocked_when_reason_mentions_pass():
    if len(TOOLS) < 2:
        pytest.skip("Multi-tool contract only")
    context = Context('{"decision":"BLOCKED","reason":"No PASS without independent review"}')
    result = asyncio.run(execute(_normal_input(), context))
    assert result["decision"] == "BLOCKED"


def test_child_blocked_remains_blocked_when_model_says_pass():
    if not DELEGATES:
        pytest.skip("Supervisor contract only")
    context = Context('{"decision":"PASS"}', {"decision": "BLOCKED", "reason": "Child found missing evidence"})
    result = asyncio.run(execute(_normal_input(), context))
    assert result["decision"] == "BLOCKED"
