"""Reusable guided-flow engine for complicated multi-step TUI tasks.

Generalizes the one-off ``handlers/first_run.py`` wizard into a step engine any
handler can drive: N ordered steps, per-step verify, Back/Skip/Quit navigation,
**resume-after-interruption** (so an SPI reboot mid-setup doesn't restart the
whole flow), and an **honest** final summary that never reports a skipped or
failed step as done.

Each step's real mutation should route through
``remediation.RemediationAction`` / ``propose_remediation`` (or the
``run_script_action`` helper here) so the propose→ratify→apply→honest-report +
admin-gate + never-crash semantics come for free — the app never ejects the
operator to a shell (the In-Domain Principle, MF018).

First consumer: ``handlers/gateway_wizard.py`` (the SF ↔ MeshForge ↔ RNS setup),
but the engine is deliberately task-agnostic — it is the reusable spine for any
"complicated task" a handler wants to make legible.

Design contract for a step's callables:
  * ``describe(ctx, state) -> str`` — what this step will do + current status.
    Read-only; may inspect ``state`` but must not mutate it.
  * ``run(ctx, state) -> StepResult`` — perform the step. May show dialogs and
    apply actions. MUST NOT raise for an expected failure (return
    ``StepResult.failed(...)``); the engine also guards unexpected exceptions so
    a step can never crash the TUI.
  * ``verify(ctx, state) -> (bool, str)`` — optional; observe the step's effect.
    A step is only reported "verified" when this returns truthy — a clean
    ``run`` with a failed ``verify`` is surfaced honestly, never averaged away.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Navigation choices returned by the per-step menu.
NAV_RUN = "run"
NAV_SKIP = "skip"
NAV_BACK = "back"
NAV_QUIT = "quit"


class StepStatus(Enum):
    """Terminal status recorded for a step (navigation is separate)."""
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


_STATUS_ICON = {
    StepStatus.DONE: "✓",
    StepStatus.SKIPPED: "↷",
    StepStatus.FAILED: "✗",
    StepStatus.PENDING: "·",
}


@dataclass
class StepResult:
    """What a step's ``run`` returns. ``data`` is merged into flow state."""
    status: StepStatus
    message: str = ""
    data: Optional[Dict[str, Any]] = None

    @classmethod
    def done(cls, message: str = "", **data) -> "StepResult":
        return cls(StepStatus.DONE, message, data or None)

    @classmethod
    def skipped(cls, message: str = "") -> "StepResult":
        return cls(StepStatus.SKIPPED, message)

    @classmethod
    def failed(cls, message: str = "") -> "StepResult":
        return cls(StepStatus.FAILED, message)


@dataclass
class WizardStep:
    """One step in a guided flow. See module docstring for the callable contract."""
    key: str
    title: str
    describe: Callable[[Any, dict], str]
    run: Callable[[Any, dict], StepResult]
    verify: Optional[Callable[[Any, dict], Tuple[bool, str]]] = None
    optional: bool = True  # whether "Skip" is offered


def run_script_action(
    label: str,
    description: str,
    argv: List[str],
    *,
    timeout: int = 120,
    requires_admin: bool = True,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
):
    """Wrap an idempotent shell/CLI backend as a ``RemediationAction``.

    This is the mechanism that brings ``configure_gateway.sh`` /
    ``install_gateway_service.sh`` / ``configure_lora.sh`` / ``provision_role.py``
    *into* the app — the operator never leaves the TUI. It is **not** a
    shell-escape (the opposite of MF018): ``argv`` only (never ``shell=True``,
    MF002), always ``timeout=`` (MF004), output captured and returned as a
    bounded tail so the result shows in-pane.

    Returns a ``RemediationAction`` whose ``apply()`` → ``(rc == 0, tail)``.
    Feed it to ``remediation.propose_remediation`` for the ratify + admin gate.
    """
    from remediation import RemediationAction

    def _apply() -> Tuple[bool, str]:
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=timeout, cwd=cwd, env=env,
            )
        except subprocess.TimeoutExpired:
            return (False, f"{argv[0]} timed out after {timeout}s")
        except (OSError, subprocess.SubprocessError) as e:
            return (False, f"could not run {argv[0]}: {e}")
        out = (r.stdout or "")
        if r.stderr:
            out += "\n[stderr]\n" + r.stderr
        tail = out.strip()[-1500:]
        ok = (r.returncode == 0)
        prefix = "" if ok else f"exit {r.returncode}\n"
        return (ok, prefix + (tail or f"{argv[0]} finished (rc={r.returncode})"))

    return RemediationAction(
        label=label, description=description, apply=_apply,
        requires_admin=requires_admin,
    )


class GuidedFlow:
    """Drive an ordered list of ``WizardStep`` with resume + honest reporting.

    Usage::

        flow = GuidedFlow("gateway", "Gateway Setup", steps)
        state = flow.run(ctx)   # blocks in the TUI, returns final state
    """

    def __init__(
        self,
        flow_id: str,
        title: str,
        steps: List[WizardStep],
        state_dir: Optional[Path] = None,
    ):
        if not steps:
            raise ValueError("GuidedFlow needs at least one step")
        self.flow_id = flow_id
        self.title = title
        self.steps = steps
        self._state_dir = state_dir  # override for tests

    # -- resume state ------------------------------------------------------

    def _state_path(self) -> Path:
        if self._state_dir is not None:
            base = self._state_dir
        else:
            from utils.paths import get_real_user_home
            base = get_real_user_home() / ".config" / "meshforge" / "wizard_state"
        return base / f"{self.flow_id}.json"

    def load_state(self) -> dict:
        """Read persisted state, or a fresh skeleton. A corrupt/absent file is
        NOT treated as "clean progress" — it yields a fresh skeleton and logs,
        never a partially-populated state that could read as done."""
        path = self._state_path()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("_completed"), dict):
                return data
            logger.warning("guided_flow %s: state file malformed, starting fresh", self.flow_id)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logger.warning("guided_flow %s: unreadable state (%s), starting fresh", self.flow_id, e)
        return {"flow_id": self.flow_id, "_completed": {}, "data": {}, "verify": {}}

    def save_state(self, state: dict) -> None:
        """Atomically persist state so an interruption resumes, never corrupts."""
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from utils.paths import atomic_write_text
            atomic_write_text(path, json.dumps(state, indent=2, sort_keys=True))
        except OSError as e:
            # Persistence failure must be visible, not silently swallowed
            # (honest_failure_modes #9) — but must not crash the flow.
            logger.error("guided_flow %s: could not save state: %s", self.flow_id, e)

    def clear_state(self) -> None:
        try:
            self._state_path().unlink()
        except (OSError, FileNotFoundError):
            pass

    # -- helpers -----------------------------------------------------------

    def _record(self, state: dict, step: WizardStep, status: StepStatus) -> None:
        state.setdefault("_completed", {})[step.key] = status.value

    def _first_unfinished_index(self, state: dict) -> int:
        completed = state.get("_completed", {})
        for i, step in enumerate(self.steps):
            if completed.get(step.key) != StepStatus.DONE.value:
                return i
        return len(self.steps)  # all done

    def _safe_run(self, step: WizardStep, ctx, state: dict) -> StepResult:
        try:
            result = step.run(ctx, state)
        except Exception as e:  # a step must never crash the TUI
            logger.exception("guided_flow %s: step %s raised", self.flow_id, step.key)
            return StepResult.failed(f"{step.title} errored: {e}")
        if not isinstance(result, StepResult):
            return StepResult.failed(f"{step.title} returned an invalid result")
        return result

    def _safe_verify(self, step: WizardStep, ctx, state: dict) -> Tuple[bool, str]:
        try:
            ok, msg = step.verify(ctx, state)
            return bool(ok), str(msg)
        except Exception as e:
            logger.exception("guided_flow %s: verify %s raised", self.flow_id, step.key)
            # An erroring verify is UNKNOWN, never a pass (honest_failure_modes #2).
            return False, f"verification errored: {e}"

    def _present_step(self, ctx, step: WizardStep, idx: int, state: dict) -> str:
        total = len(self.steps)
        prior = state.get("_completed", {}).get(step.key)
        status_line = ""
        if prior:
            status_line = f"\n\n(previously: {prior})"
        body = step.describe(ctx, state) + status_line
        choices: List[Tuple[str, str]] = [(NAV_RUN, "▶ Run this step")]
        if step.optional:
            choices.append((NAV_SKIP, "Skip this step"))
        if idx > 0:
            choices.append((NAV_BACK, "◀ Back"))
        choices.append((NAV_QUIT, "Quit wizard (progress saved)"))
        sel = ctx.dialog.menu(f"Step {idx + 1}/{total}: {step.title}", body, choices)
        return sel or NAV_QUIT  # cancel == quit

    def _summary(self, ctx, state: dict) -> None:
        completed = state.get("_completed", {})
        verify = state.get("verify", {})
        lines = [f"{self.title} — summary", ""]
        any_failed = False
        for step in self.steps:
            raw = completed.get(step.key)
            status = StepStatus(raw) if raw in {s.value for s in StepStatus} else StepStatus.PENDING
            icon = _STATUS_ICON[status]
            line = f"  {icon}  {step.title} — {status.value}"
            v = verify.get(step.key)
            if v is not None:
                line += "  [verified]" if v.get("ok") else "  [NOT verified]"
                if not v.get("ok"):
                    any_failed = True
            if status == StepStatus.FAILED:
                any_failed = True
            lines.append(line)
        lines.append("")
        if any_failed:
            lines.append("Not everything is confirmed — re-run the ✗ / NOT-verified steps.")
        elif self._first_unfinished_index(state) >= len(self.steps):
            lines.append("All steps completed and (where checked) verified.")
        else:
            lines.append("Some steps were skipped or not reached — resume any time.")
        ctx.dialog.textbox(self.title, "\n".join(lines))

    # -- main loop ---------------------------------------------------------

    def run(self, ctx) -> dict:
        """Run the flow in the TUI. Returns the final state dict."""
        state = self.load_state()
        start_idx = 0
        resume_from = self._first_unfinished_index(state)
        if state.get("_completed") and 0 < resume_from < len(self.steps):
            choice = ctx.dialog.menu(
                self.title,
                f"A previous run of this wizard is saved (up to step {resume_from}).\n\n"
                "Resume where you left off, or start over?",
                [("resume", f"Resume from step {resume_from + 1}"),
                 ("restart", "Start over (clears saved progress)"),
                 ("cancel", "Cancel")],
            )
            if choice == "cancel" or choice is None:
                return state
            if choice == "restart":
                self.clear_state()
                state = self.load_state()
            else:
                start_idx = resume_from

        idx = start_idx
        total = len(self.steps)
        while 0 <= idx < total:
            step = self.steps[idx]
            nav = self._present_step(ctx, step, idx, state)

            if nav == NAV_QUIT:
                if ctx.dialog.yesno(
                    "Quit wizard",
                    "Leave the wizard now? Progress so far is saved — you can resume later.",
                ):
                    self.save_state(state)
                    break
                continue
            if nav == NAV_BACK:
                idx = max(0, idx - 1)
                continue
            if nav == NAV_SKIP:
                self._record(state, step, StepStatus.SKIPPED)
                self.save_state(state)
                idx += 1
                continue

            # NAV_RUN
            result = self._safe_run(step, ctx, state)
            if result.data:
                state.setdefault("data", {}).update(result.data)
            self._record(state, step, result.status)

            if step.verify is not None and result.status == StepStatus.DONE:
                vok, vmsg = self._safe_verify(step, ctx, state)
                state.setdefault("verify", {})[step.key] = {"ok": vok, "msg": vmsg}
                ctx.report_action(
                    vok,
                    f"{step.title} — verified", vmsg or "Verified.",
                    fail_title=f"{step.title} — NOT verified",
                    fail_body=vmsg or "The step ran, but its effect could not be observed. "
                                     "Treat this step as unconfirmed.",
                )
            elif result.message:
                ctx.report_action(
                    result.status != StepStatus.FAILED,
                    step.title, result.message,
                    fail_title=f"{step.title} — failed", fail_body=result.message,
                )

            self.save_state(state)
            if result.status in (StepStatus.DONE, StepStatus.SKIPPED):
                idx += 1
            # FAILED → stay on this step so the operator can retry / skip / back.

        self._summary(ctx, state)
        return state
