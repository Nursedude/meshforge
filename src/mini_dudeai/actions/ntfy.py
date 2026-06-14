"""Push to an ntfy.sh topic.

Edge-up: send a notification with title + templated message + tags + priority.
Edge-down: send a quieter "cleared" notice with priority=min (so the operator
knows things recovered without being woken up).

Per-rule overrides via rule.action.{title,message,priority,tags} take
precedence over the action's defaults. This is what lets one NtfyAction
instance serve many rules with different framing.
"""
from __future__ import annotations

import urllib.error
import urllib.request

from ..sources.base import Condition
from .base import Action, Outcome


class NtfyAction(Action):
    """POST to an ntfy.sh topic.

    Args:
        topic: ntfy topic name. Required — no operator-default. (Public default
            would leak operator-specific values; MeshForge lint MF014 forbids.)
        base_url: ntfy server (default https://ntfy.sh)
        default_priority: priority for edge-up if rule doesn't override (default "default")
        default_tags: tags for edge-up if rule doesn't override
        timeout_s: HTTP timeout (default 8)
        token: optional bearer token for a reserved/auth'd topic (ntfy Pro or a
            self-hosted server with ACLs). None => unauthenticated, identical to
            the free-tier behavior. Resolve it from a `token_env` env-var NAME in
            the seed spec (keeps the secret out of the config), never a literal.
    """

    name = "ntfy"

    def __init__(
        self,
        topic: str,
        base_url: str = "https://ntfy.sh",
        default_priority: str = "default",
        default_tags: list[str] | None = None,
        timeout_s: float = 8.0,
        token: str | None = None,
    ) -> None:
        if not topic:
            raise ValueError("NtfyAction requires a topic")
        self.topic = topic
        self.url = f"{base_url.rstrip('/')}/{topic}"
        self.default_priority = default_priority
        self.default_tags = default_tags or ["robot_face"]
        self.timeout_s = timeout_s
        self.token = token or None

    def execute(self, rule: dict, cond: Condition, transition: str) -> Outcome:
        action_cfg = rule.get("action") or {}
        if transition == "edge_up":
            title = action_cfg.get("title") or f"[mini-dudeai] {rule['id']}"
            tpl = action_cfg.get("message") or "{subject}: {detail}"
            try:
                message = tpl.format(subject=cond.subject, detail=cond.detail)
            except (KeyError, IndexError):
                message = f"{cond.subject}: {cond.detail}"
            note = (rule.get("annotation") or "").strip()
            if note:
                message = f"{message}\n\n{note}"
            priority = action_cfg.get("priority", self.default_priority)
            tags = action_cfg.get("tags") or self.default_tags
        else:  # edge_down
            title = f"[mini-dudeai] cleared: {rule['id']}"
            message = f"{cond.subject}: condition cleared"
            priority = "min"
            tags = ["white_check_mark"]
        return self._post(title, message, priority, tags, transition)

    def _post(self, title: str, message: str, priority: str,
              tags: list[str], transition: str) -> Outcome:
        body = (message or "").encode("utf-8", "replace")
        try:
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Title", title.encode("ascii", "replace").decode("ascii"))
            req.add_header("Priority", priority)
            if tags:
                req.add_header("Tags", ",".join(tags))
            if self.token:
                req.add_header("Authorization", f"Bearer {self.token}")
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                r.read()
            return Outcome(action=f"ntfy_{transition}", ok=True)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return Outcome(
                action=f"ntfy_{transition}",
                ok=False,
                error=f"{type(e).__name__}: {e}",
            )
