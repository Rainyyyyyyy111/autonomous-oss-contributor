"""Offline, scope-aware HTML snapshot. No models, remote assets or write actions."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .desktop import Desktop
from .environments import Environments


def cell(value: object) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def table(headers: list[str], rows: list[list]) -> str:
    return ("<div class='scroll'><table><thead><tr>" +
            "".join(f"<th>{cell(h)}</th>" for h in headers) + "</tr></thead><tbody>" +
            "".join("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>" for row in rows) +
            "</tbody></table></div>" if rows else "<p class='muted'>暂无记录 / No records</p>")


def snapshot(desktop: Desktop) -> dict:
    allowed = desktop.scoped_ids()
    tasks = [t for t in desktop.store.tasks() if
             (allowed is None and desktop.config.repository(t["repo"]) is not None)
             or (allowed is not None and t["id"] in allowed)]
    ids = {t["id"] for t in tasks}
    # Selection is computed without persisting anything or expanding monitor scope.
    queue = [t for t in desktop.engine.selector.queue() if t["id"] in ids]
    return {
        "generated_at": datetime.now(ZoneInfo(desktop.config.timezone)).isoformat(),
        "health": desktop.status(), "tasks": tasks, "queue": queue,
        "outcomes": [r for r in desktop.engine.learning.outcomes() if r["task"] in ids],
        "feedback": [r for r in desktop.engine.learning.records() if r["task"] in ids],
        "environments": Environments(desktop.store).reports(),
    }


def ci_summary(task: dict) -> str:
    data = task["data"]
    observation = data.get("desktop_observation") or {}
    if not observation:
        return "not observed"
    if observation.get("head_sha") != (data.get("pr") or {}).get("head_sha"):
        return "different head; do not infer current CI"
    if observation.get("waiting"):
        return observation["waiting"]
    checks = observation.get("ci", [])
    if not checks:
        return "no remote checks observed"
    states = Counter(str(c.get("conclusion") or c.get("status") or "unknown") for c in checks)
    return ", ".join(f"{state}: {count}" for state, count in sorted(states.items()))


def render(data: dict) -> str:
    health = data["health"]
    counts = Counter(t["status"] for t in data["tasks"])
    outcomes = Counter(r["state"] for r in data["outcomes"])
    budgets = health["budget_remaining"]
    cards = [("当前状态 / Status", health["reason"]),
             ("剩余尝试 / Attempts left", budgets["attempts"]),
             ("剩余发布 / PRs left", budgets["publications"]),
             ("模型分钟 / Model minutes left", round(budgets["model_seconds"] / 60, 1))]
    parts = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
             "<title>OSS Contributor · Local status</title><style>",
             "body{font:15px system-ui;margin:0;background:#f4f6fa;color:#172337}main{max-width:1200px;margin:auto;padding:40px 24px}h1{font-size:30px}h2{margin-top:36px;font-size:20px}.muted{color:#58667b}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}.card{background:white;border:1px solid #dfe5ef;padding:20px;border-radius:12px}.card strong{display:block;font-size:22px;margin-top:12px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #e1e7ef;max-width:480px;overflow-wrap:anywhere}th{color:#476183}details{margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#1857a2}",
             "</style><main><h1>Autonomous OSS Contributor</h1>",
             f"<p class='muted'>本地只读快照 / Offline snapshot · {cell(data['generated_at'])}</p>",
             "<div class='cards'>"]
    parts += [f"<div class='card'>{cell(label)}<strong>{cell(value)}</strong></div>" for label, value in cards]
    parts += ["</div>", f"<p>额度重置 / Reset: {cell(health['budget_resets_at'])}</p>",
              "<details><summary>运行范围与任务统计 / Scope and counts</summary><pre>" +
              cell(json.dumps({"scope": health["scope"], "tasks": dict(counts),
                               "remote_outcomes": dict(outcomes)}, ensure_ascii=False, indent=2)) +
              "</pre></details>", "<h2>任务与 PR / Tasks and PRs</h2>"]
    parts += [table(["Task", "Repository / Issue", "State", "PR", "Verification", "Remote CI", "Wait / Reason"], [
        [t["id"], f"{t['repo']} #{t['issue']}", t["status"],
         (t["data"].get("pr") or {}).get("url"),
         "local checks passed" if t["data"].get("report", {}).get("passed") else "not verified",
         ci_summary(t),
         t["data"].get("waiting") or t["data"].get("reason", "")] for t in data["tasks"]])]
    parts += ["<h2>候选顺序 / Candidate queue</h2>",
              "<p class='muted'>分数用于排序；完整归属检查和实际测试仍决定能否发布。无远端检查不代表 CI 成功。</p>",
              table(["Task", "Repository / Issue", "Score", "Deferred", "Signals"], [
                  [t["id"], f"{t['repo']} #{t['issue']}", t["selection"]["score"],
                   "; ".join(t["selection"]["deferred"]),
                   "; ".join(s["reason"] for s in t["selection"]["signals"])] for t in data["queue"]]),
              "<h2>活动聊天 / Active chats</h2>",
              table(["Task", "State", "Thread", "Seconds left"], [
                  [j["task"], j["state"], j["thread_id"], round(j["remaining_seconds"])]
                  for j in health["active_jobs"]]),
              "<h2>反馈证据 / Feedback evidence</h2>",
              table(["Task", "Categories", "Locally addressed", "Observed outcome"], [
                  [r["task"], ", ".join(r["categories"]), r["addressed"], r["last_pr_state"]]
                  for r in data["feedback"]]),
              "<h2>环境预检 / Environment preparation</h2>",
              table(["Image", "Ready at last probe", "Detail"], [
                  [r["image"], r["ok"], r.get("detail", "")] for r in data["environments"]]),
              "<p class='muted'>快照不会自动刷新。合并和关闭来自观察记录；关闭本身不证明修复质量。</p></main></html>"]
    return "\n".join(parts)
