from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

from .config import Discovery
from .models import Issue, NotificationBatch, PRState, ReviewEvent
from .process import ContributorError, PublicationConflict, RateLimited, run_bytes, run_command
from .safety import MAX_ARCHIVE, STOP, unpack_archive

ISSUE_QUERY = """query($owner:String!,$name:String!,$number:Int!){
 repository(owner:$owner,name:$name){isArchived isFork visibility
 issue(number:$number){number title body url state
 assignees(first:1){totalCount}
 labels(first:50){nodes{name} pageInfo{hasNextPage}}
 comments(first:100){nodes{body authorAssociation author{login}} pageInfo{hasNextPage}}
 timelineItems(last:100,itemTypes:[CROSS_REFERENCED_EVENT]){
 pageInfo{hasPreviousPage} nodes{... on CrossReferencedEvent{
 source{... on PullRequest{number title state url}}}}}}}}"""


class GitHub:
    def __init__(self, timezone: str = "Asia/Singapore"):
        self._login: str | None = None
        self.timezone = timezone
        self.deadline: float | None = None
        self.before_write: Callable[[], None] | None = None
        self.response_headers: dict[str, str] = {}
        self.response_status: int = 0
        self.notification_poll_interval: int = 60

    @contextmanager
    def bounded(self, deadline: float):
        previous = self.deadline
        self.deadline = min(previous, deadline) if previous is not None else deadline
        try:
            yield
        finally:
            self.deadline = previous

    def timeout(self, maximum: float) -> float:
        if self.deadline is None:
            return maximum
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ContributorError("GitHub task deadline exceeded")
        return min(maximum, remaining)

    def api(
        self, endpoint: str, *, method: str = "GET", data: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if method != "GET" and endpoint != "graphql" and self.before_write:
            self.before_write()
        argv = ["gh", "api", "--include", "--method", method, endpoint]
        for key, value in (headers or {}).items():
            argv += ["--header", f"{key}: {value}"]
        if data is not None:
            argv += ["--input", "-"]
        result = run_command(
            argv,
            input_text=json.dumps(data) if data is not None else None,
            timeout=self.timeout(60),
            max_output=2_000_000,
        )
        # gh --include returns HTTP headers followed by JSON; redirects can add blocks.
        match = list(re.finditer(r"(?m)^\s*[\[{]", result.output))
        header = result.output[: match[0].start()] if match else result.output
        # Keep only the final HTTP header block (gh may follow a redirect).
        blocks = re.split(r"(?im)^HTTP/[^\r\n]+", header)
        self.response_headers = {
            key.lower(): value.strip()
            for key, value in re.findall(r"(?m)^([\w-]+):[ \t]*([^\r\n]*)", blocks[-1])
        }
        statuses = re.findall(r"(?im)^HTTP/\S+\s+(\d{3})", header)
        self.response_status = int(statuses[-1]) if statuses else 0
        if self.response_status == 304:
            return None
        if result.code:
            if (
                "rate limit" in result.output.lower()
                or "HTTP 429" in result.output
                or re.search(r"(?im)^retry-after:", header)
            ):
                retry = re.search(r"(?im)^retry-after:\s*(\d+)", header)
                reset = re.search(r"(?im)^x-ratelimit-reset:\s*(\d+)", header)
                wait = (
                    int(retry[1])
                    if retry
                    else max(60, int(reset[1]) - int(time.time()))
                    if reset
                    else 600
                )
                raise RateLimited(wait)
            raise ContributorError(f"GitHub {method} {endpoint}: {result.output[-1500:]}")
        if not match:
            return None
        try:
            return json.loads(result.output[match[0].start() :])
        except ValueError:
            raise ContributorError("GitHub returned invalid or oversized JSON evidence") from None

    def login(self) -> str:
        if self._login is None:
            self._login = self.api("user")["login"]
        return self._login

    def notification_batch(
        self, since: str, last_modified: str = "", max_pages: int = 10
    ) -> NotificationBatch:
        """Read the inbox without changing GitHub read state or following subject URLs."""
        endpoint = "notifications?" + urlencode({
            "all": "true", "participating": "false", "since": since, "per_page": 50,
        })
        result = NotificationBatch()
        self.notification_poll_interval = 60
        visited = set()
        for page in range(max_pages):
            if endpoint in visited:
                raise ContributorError("GitHub notification pagination loop")
            visited.add(endpoint)
            rows = self.api(
                endpoint, headers={"If-Modified-Since": last_modified}
                if page == 0 and last_modified else None,
            )
            interval = self.response_headers.get("x-poll-interval", "60")
            if not interval.isdigit():
                raise ContributorError("invalid GitHub notification poll interval")
            result.poll_interval = max(result.poll_interval, int(interval))
            self.notification_poll_interval = result.poll_interval
            if page == 0:
                result.last_modified = self.response_headers.get("last-modified", "")
                result.not_modified = self.response_status == 304
            if result.not_modified:
                return result
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ContributorError("invalid GitHub notifications response")
            result.rows.extend(rows)
            link = self.response_headers.get("link", "")
            following = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if not following:
                return result
            target = urlparse(following[1])
            query = parse_qs(target.query)
            if (
                target.scheme != "https" or target.netloc != "api.github.com"
                or target.path != "/notifications" or target.fragment
                or query.get("since") != [since]
                or query.get("all") != ["true"]
                or query.get("participating") != ["false"]
                or query.get("per_page") != ["50"]
                or not query.get("page", [""])[0].isdigit()
            ):
                raise ContributorError("untrusted GitHub notification pagination URL")
            endpoint = target.path.lstrip("/") + "?" + target.query
        raise ContributorError("GitHub notification pagination bound exceeded; cursor unchanged")

    def discover(self, options: Discovery) -> list[dict]:
        since = datetime.now(ZoneInfo(self.timezone)).date() - timedelta(days=options.pushed_days)
        query = f"language:Python stars:>={options.min_stars} pushed:>={since} archived:false is:public fork:false"
        result = self.api(
            "search/repositories?"
            + urlencode(
                {"q": query, "sort": "stars", "order": "desc", "per_page": options.max_repositories}
            )
        )
        return [
            {
                "fullName": row["full_name"],
                "stargazersCount": row["stargazers_count"],
                "description": row.get("description"),
                "url": row["html_url"],
            }
            for row in result["items"]
        ]

    def issue_numbers(self, repo: str, limit: int) -> list[int]:
        result = self.api(
            "search/issues?"
            + urlencode(
                {
                    "q": f"repo:{repo} is:issue is:open label:bug",
                    "sort": "updated",
                    "per_page": limit,
                }
            )
        )
        return [row["number"] for row in result["items"]]

    def optional_file(self, repo: str, path: str, ref: str | None = None) -> str:
        endpoint = f"repos/{repo}/contents/{path}" + (f"?ref={ref}" if ref else "")
        try:
            data = self.api(endpoint)
        except ContributorError as exc:
            if "404" in str(exc):
                return ""
            raise
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            raise ContributorError("unsupported GitHub policy file")
        content = base64.b64decode(data["content"])
        if len(content) > 100_000:
            raise ContributorError("policy file too large")
        return content.decode("utf-8")

    def issue(self, repo: str, number: int) -> Issue:
        owner, name = repo.split("/")
        response = self.api(
            "graphql",
            method="POST",
            data={
                "query": ISSUE_QUERY,
                "variables": {"owner": owner, "name": name, "number": number},
            },
        )
        if response.get("errors"):
            raise ContributorError("Issue evidence could not be fully queried")
        repository = response["data"]["repository"]
        if not repository or not repository.get("issue"):
            return Issue(
                repo=repo.lower(),
                number=number,
                title="Unavailable issue",
                state="closed",
                conflicts=["issue or repository is unavailable"],
            )
        raw = repository["issue"]
        conflicts = []
        if repository["isArchived"] or repository["visibility"] != "PUBLIC":
            conflicts.append("repository must be public and active")
        if raw["assignees"]["totalCount"]:
            conflicts.append("issue has an assignee")
        if (
            raw["comments"]["pageInfo"]["hasNextPage"]
            or raw["timelineItems"]["pageInfo"]["hasPreviousPage"]
            or raw["labels"]["pageInfo"]["hasNextPage"]
        ):
            conflicts.append("issue history is truncated; ownership is uncertain")
        for event in raw["timelineItems"]["nodes"]:
            source = (event or {}).get("source", {})
            if source.get("state") == "OPEN":
                conflicts.append(f"linked open PR: {source.get('url')}")
        linked = self.api(
            "search/issues?"
            + urlencode({"q": f"repo:{repo} is:pr is:open {number} in:body", "per_page": 100})
        )
        for pr in linked["items"]:
            if re.search(rf"(?:#|/issues/){number}\b", pr.get("body", "")):
                conflicts.append(f"open PR references issue: {pr['html_url']}")
        policy_parts = []
        for path in (
            "CONTRIBUTING.md",
            "CONTRIBUTING.rst",
            ".github/CONTRIBUTING.md",
            ".github/CONTRIBUTING.rst",
            "AI_POLICY.md",
            "AGENTS.md",
        ):
            text = self.optional_file(repo, path)
            if text:
                policy_parts.append(f"--- {path} (untrusted repository text) ---\n{text}")
        template = ""
        for path in (
            ".github/PULL_REQUEST_TEMPLATE.md",
            "PULL_REQUEST_TEMPLATE.md",
            "docs/PULL_REQUEST_TEMPLATE.md",
            ".github/pull_request_template.md",
        ):
            template = self.optional_file(repo, path)
            if template:
                break
        return Issue(
            repo=repo.lower(),
            number=number,
            title=raw["title"],
            body=raw["body"],
            url=raw["url"],
            state=raw["state"].lower(),
            labels=[x["name"] for x in raw["labels"]["nodes"]],
            comments=raw["comments"]["nodes"],
            conflicts=conflicts,
            policy="\n".join(policy_parts),
            pr_template=template,
        )

    def base(self, repo: str) -> tuple[str, str]:
        raw = self.api(f"repos/{repo}")
        branch = raw["default_branch"]
        commit = self.api(f"repos/{repo}/commits/{branch}")
        return branch, commit["sha"]

    def snapshot(self, repo: str, sha: str) -> dict[str, str]:
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ContributorError("invalid base SHA")
        code, payload, _ = run_bytes(
            ["gh", "api", f"repos/{repo}/tarball/{sha}"],
            timeout=self.timeout(120),
            max_output=MAX_ARCHIVE,
            reject_truncation=True,
            merge_stderr=False,
        )
        if code:
            raise ContributorError("repository download failed")
        return unpack_archive(payload)

    def _pages(self, endpoint: str) -> list[dict]:
        result = []
        for page in range(1, 11):
            rows = self.api(
                endpoint + ("&" if "?" in endpoint else "?") + f"per_page=100&page={page}"
            )
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ContributorError("GitHub evidence exceeds pagination bound")

    def pr_state(self, repo: str, number: int) -> PRState:
        raw = self.api(f"repos/{repo}/pulls/{number}")
        head = raw["head"]["sha"]
        state = "merged" if raw.get("merged_at") else raw["state"]
        events = []
        stop = False
        comments = self._pages(f"repos/{repo}/issues/{number}/comments")
        reviews = self._pages(f"repos/{repo}/pulls/{number}/reviews")
        inline = self._pages(f"repos/{repo}/pulls/{number}/comments")
        for item in comments + reviews + inline:
            if item.get("author_association") not in {"OWNER", "MEMBER", "COLLABORATOR"}:
                continue
            body = item.get("body") or ""
            stop |= bool(STOP.search(body))
            if item in comments or (
                item.get("commit_id") == head and item.get("state") != "DISMISSED"
            ):
                if body.strip():
                    events.append(
                        ReviewEvent(
                            id=f"review:{item.get('node_id', item['id'])}:{item.get('updated_at', item.get('submitted_at', ''))}",
                            body=body,
                        )
                    )
        checks = self.api(f"repos/{repo}/commits/{head}/check-runs?per_page=100")
        statuses = self.api(f"repos/{repo}/commits/{head}/status?per_page=100")
        waiting = ""
        ci = []
        # First-time fork contributions can require approval before GitHub creates
        # any check runs. Looking only at checks would incorrectly report no wait.
        try:
            actions = self.api(f"repos/{repo}/actions/runs?head_sha={head}&per_page=100")
        except RateLimited:
            raise
        except ContributorError:
            waiting = "GitHub Actions evidence unavailable"
        else:
            if actions.get("total_count", 0) > 100:
                waiting = "GitHub Actions evidence truncated"
            for action in actions.get("workflow_runs", []):
                if action.get("head_sha") != head:
                    continue
                ci.append({"kind": "actions", "id": action.get("id"), "head_sha": head,
                           "status": action.get("status"), "conclusion": action.get("conclusion")})
                if action.get("conclusion") == "action_required":
                    waiting = "CI awaiting maintainer authorization"
                elif action.get("status") != "completed":
                    waiting = waiting or "CI pending"
        if checks.get("total_count", 0) > 100 or statuses.get("total_count", 0) > 100:
            waiting = "CI evidence truncated"
        for check in checks.get("check_runs", []):
            if check.get("head_sha") != head:
                continue
            ci.append({"kind": "check", "id": check.get("id"), "head_sha": head,
                       "status": check.get("status"), "conclusion": check.get("conclusion")})
            if check.get("status") != "completed" or check.get("conclusion") in {
                "action_required",
                "stale",
            }:
                waiting = "CI pending or awaiting maintainer authorization"
                continue
            if check.get("conclusion") in {"failure", "timed_out"}:
                # Read GitHub Actions logs only for the exact current head, never external URLs.
                run_match = re.search(r"/actions/runs/(\d+)", check.get("details_url") or "")
                if not run_match:
                    waiting = "failed CI logs unavailable"
                    continue
                run = self.api(f"repos/{repo}/actions/runs/{run_match[1]}")
                if run["head_sha"] != head:
                    continue
                if run.get("conclusion") == "action_required":
                    waiting = "CI awaiting maintainer authorization"
                    continue
                log = run_command(["gh", "run", "view", run_match[1], "-R", repo, "--log-failed"])
                if log.code or not log.output.strip():
                    waiting = "failed CI logs unavailable"
                else:
                    events.append(
                        ReviewEvent(
                            id=f"ci:{check['id']}:{head}", kind="ci", body=log.output[-30_000:]
                        )
                    )
        for status in statuses.get("statuses", []):
            ci.append({"kind": "status", "id": status.get("id"), "head_sha": head,
                       "status": status["state"], "conclusion": None})
            if status["state"] in {"failure", "error"}:
                waiting = waiting or "external CI failure requires accessible logs"
            elif status["state"] == "pending":
                waiting = waiting or "external CI pending"
        return PRState.model_validate(
            dict(
                number=number,
                url=raw["html_url"],
                state=state,
                head_sha=head,
                body=raw.get("body") or "",
                events=events,
                waiting=waiting,
                stop=stop,
                ci=ci,
            )
        )

    def update_pr_body(
        self, repo: str, number: int, body: str, expected_head: str, expected_body: str
    ):
        """Edit only the description; refuse observed intervening human edits on retry."""
        endpoint = f"repos/{repo}/pulls/{number}"
        raw = self.api(endpoint)
        if raw["state"] != "open" or raw.get("merged_at"):
            raise PublicationConflict("PR closed before description update")
        if raw["head"]["sha"] != expected_head:
            raise PublicationConflict("PR Head changed before description update")
        current = raw.get("body") or ""
        if current == body:
            return  # A previous PATCH may have succeeded before its response was lost.
        if current != expected_body:
            raise PublicationConflict("PR description changed outside this agent")
        self.api(endpoint, method="PATCH", data={"body": body})

    def find_pr(self, repo: str, branch: str) -> dict | None:
        query = urlencode({"state": "all", "head": f"{self.login()}:{branch}", "per_page": 100})
        rows = self.api(f"repos/{repo}/pulls?{query}")
        if len(rows) > 1:
            raise ContributorError("multiple PRs found for deterministic branch")
        return rows[0] if rows else None

    def publish(
        self,
        repo: str,
        base_branch: str,
        base_sha: str,
        changes: dict[str, str | None],
        branch: str,
        title: str,
        body: str,
        previous_head: str | None = None,
    ) -> dict:
        owner = self.login()
        fork_name = ("oss-" + repo.replace("/", "-"))[:100]
        fork = f"{owner}/{fork_name}"
        try:
            metadata = self.api(f"repos/{fork}")
        except ContributorError as exc:
            if "404" not in str(exc):
                raise
            created = self.api(
                f"repos/{repo}/forks",
                method="POST",
                data={"name": fork_name, "default_branch_only": True},
            )
            # GitHub may return an existing personal fork under a different name.
            fork = created.get("full_name", fork)
            metadata = None
            for _ in range(10):
                time.sleep(2)
                try:
                    metadata = self.api(f"repos/{fork}")
                    break
                except ContributorError as error:
                    if "404" not in str(error):
                        raise
            if metadata is None:
                raise ContributorError(
                    "fork is still provisioning; publication can be retried"
                ) from None
        if (
            metadata.get("parent", {}).get("full_name", "").lower() != repo.lower()
            or metadata["owner"]["login"].lower() != owner.lower()
        ):
            raise PublicationConflict("fork name belongs to a different repository")
        commit = self.api(f"repos/{repo}/git/commits/{base_sha}")
        tree_entries = []
        for path, content in changes.items():
            entry: dict[str, Any] = {"path": path, "mode": "100644", "type": "blob"}
            if content is None:
                entry["sha"] = None
            else:
                entry["sha"] = self.api(
                    f"repos/{fork}/git/blobs",
                    method="POST",
                    data={"content": content, "encoding": "utf-8"},
                )["sha"]
            tree_entries.append(entry)
        tree = self.api(
            f"repos/{fork}/git/trees",
            method="POST",
            data={"base_tree": commit["tree"]["sha"], "tree": tree_entries},
        )["sha"]
        try:
            ref = self.api(f"repos/{fork}/git/ref/heads/{branch}")
        except ContributorError as exc:
            if "404" not in str(exc):
                raise
            ref = None
        digest = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
        marker = f"OSS-Contributor-Artifact: {digest}"
        current = None
        if ref:
            current = self.api(f"repos/{fork}/git/commits/{ref['object']['sha']}")
        if current and current["tree"]["sha"] == tree and marker in current["message"]:
            head_sha = current["sha"]  # recover push-before-PR and revision-before-save crashes
        else:
            if current and current["sha"] != previous_head:
                raise PublicationConflict("remote branch changed independently; refusing overwrite")
            parents = (
                [previous_head, base_sha]
                if previous_head and previous_head != base_sha
                else [base_sha]
            )
            head_sha = self.api(
                f"repos/{fork}/git/commits",
                method="POST",
                data={"message": f"{title}\n\n{marker}", "tree": tree, "parents": parents},
            )["sha"]
            if ref:
                self.api(
                    f"repos/{fork}/git/refs/heads/{branch}",
                    method="PATCH",
                    data={"sha": head_sha, "force": False},
                )
            else:
                self.api(
                    f"repos/{fork}/git/refs",
                    method="POST",
                    data={"ref": f"refs/heads/{branch}", "sha": head_sha},
                )
        existing = self.find_pr(repo, branch)
        if existing:
            if existing["state"] != "open":
                raise PublicationConflict("existing PR is closed; refusing a replacement")
            self.api(
                f"repos/{repo}/pulls/{existing['number']}", method="PATCH", data={"body": body}
            )
            return {"number": existing["number"], "url": existing["html_url"], "head_sha": head_sha}
        pr = self.api(
            f"repos/{repo}/pulls",
            method="POST",
            data={
                "title": title,
                "body": body,
                "head": f"{owner}:{branch}",
                "base": base_branch,
                "draft": False,
                "maintainer_can_modify": True,
            },
        )
        return {"number": pr["number"], "url": pr["html_url"], "head_sha": head_sha}
