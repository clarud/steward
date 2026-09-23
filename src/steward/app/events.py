"""Route normalized transport events to the live Steward applications."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from steward.app.agent import StewardToolAgentApplication
from steward.app.intake import (
    StewardCaptureApplication,
    StewardDriveImportApplication,
    StewardGmailImportApplication,
    StewardProvisionalIntakeApplication,
)
from steward.app.privacy import StewardPrivacyApplication
from steward.app.question import StewardQuestionApplication
from steward.app.read import StewardReadApplication
from steward.app.roots import StewardMoveReconciliationApplication, StewardRootsApplication
from steward.capture import CaptureResult
from steward.events import IncomingEvent
from steward.intent import Intent, IntentResolver
from steward.presentation import PresentedReply


class StewardEventApplication:
    """Route normalized events through one explicit intent decision."""

    def __init__(
        self,
        question_application: StewardQuestionApplication,
        capture_application: StewardCaptureApplication,
        intent_resolver: IntentResolver | None = None,
        drive_import_application: StewardDriveImportApplication | None = None,
        gmail_import_application: StewardGmailImportApplication | None = None,
        read_application: StewardReadApplication | None = None,
        provisional_intake_application: StewardProvisionalIntakeApplication | None = None,
        tool_agent_application: StewardToolAgentApplication | None = None,
        roots_application: StewardRootsApplication | None = None,
        move_reconciliation_application: StewardMoveReconciliationApplication | None = None,
        privacy_application: StewardPrivacyApplication | None = None,
    ) -> None:
        self._question_application = question_application
        self._capture_application = capture_application
        self._intent_resolver = intent_resolver or IntentResolver()
        self._drive_import_application = drive_import_application
        self._gmail_import_application = gmail_import_application
        self._read_application = read_application
        self._provisional_intake_application = provisional_intake_application
        self._tool_agent_application = tool_agent_application
        self._roots_application = roots_application
        self._move_reconciliation_application = move_reconciliation_application
        self._privacy_application = privacy_application

    def handle(self, event: IncomingEvent) -> str | PresentedReply:
        if self._privacy_application is not None:
            privacy_response = self._privacy_application.handle_command(event)
            if privacy_response is not None:
                return privacy_response
            privacy_followup = self._privacy_application.natural_source_privacy_command(event)
            if privacy_followup is not None:
                privacy_response = self._privacy_application.handle_command(
                    replace(event, text=privacy_followup)
                )
                if privacy_response is not None:
                    return privacy_response
        if self._roots_application is not None:
            root_reference = self._roots_application.resolve_root_reference(event)
            if root_reference is not None:
                return root_reference
            roots_response = self._roots_application.handle_command(event)
            if roots_response is not None:
                return roots_response
        if self._move_reconciliation_application is not None:
            move_response = self._move_reconciliation_application.handle_command(event)
            if move_response is not None:
                return move_response
        if self._tool_agent_application is not None:
            tool_response = self._tool_agent_application.handle_command(event)
            if tool_response is not None:
                return tool_response
        if self._provisional_intake_application is not None:
            intake_event = self._provisional_intake_application.reply_save_event(event) or event
            intake_response = self._provisional_intake_application.handle_command(intake_event)
            if isinstance(intake_response, CaptureResult):
                return self._capture_application.format_result(intake_response)
            if intake_response is not None:
                return intake_response
            intake_followup = self._provisional_intake_application.handle_followup(event)
            if intake_followup is not None:
                return intake_followup
        if self._read_application is not None:
            source_reference = self._read_application.resolve_source_reference(event)
            if source_reference is not None:
                return source_reference
            activity_reference = self._read_application.resolve_activity_reference(event)
            if activity_reference is not None:
                return activity_reference
            read_response = self._read_application.handle_command(event)
            if read_response is not None:
                return read_response
        if self._drive_import_application is not None:
            drive_response = self._drive_import_application.handle_command(event)
            if drive_response is not None:
                return drive_response
        if self._gmail_import_application is not None:
            gmail_response = self._gmail_import_application.handle_command(event)
            if gmail_response is not None:
                return gmail_response
        decision = self._intent_resolver.resolve(event)
        if decision.primary_intent is Intent.SEARCH:
            if self._read_application is None:
                return "Local search is not configured for this Steward process."
            return self._read_application.search(
                self._search_terms(event.text or "", natural_language=True)
            )
        if decision.primary_intent in {Intent.INSPECT, Intent.ORGANIZE}:
            if self._read_application is None:
                return "Local inspection is not configured for this Steward process."
            if "activity" in decision.referenced_objects:
                return self._read_application.activity("")
            # Codex organises files; Steward shows what is waiting in Inbox.
            return self._read_application.inbox(1)
        if decision.primary_intent is Intent.ASK:
            # Steward promises local, evidence-backed answers, so questions use
            # the grounded retrieval graph. `/agent ...` remains the explicit
            # tool-loop entry point.
            return self._question_application.handle(event)
        if decision.primary_intent is Intent.CAPTURE:
            try:
                result = self._capture_application.capture(event)
            except ValueError as error:
                return str(error)
            return self._capture_application.format_result(result)
        if (
            decision.primary_intent is Intent.UNKNOWN
            and self._provisional_intake_application is not None
            and self._provisional_intake_application.should_propose_text(event)
        ):
            return self._provisional_intake_application.begin_text(event)
        return (
            "I am not sure what you want. Try /help, ask a question, "
            "or say `find ...` or `show my inbox`."
        )

    def handle_file(self, event: IncomingEvent, original_path: Path) -> str:
        """Documents are deterministic capture signals after adapter validation."""
        if (event.text or "").strip().startswith("/save") or self._provisional_intake_application is None:
            return self._capture_application.format_result(
                self._capture_application.capture_file(event, original_path)
            )
        return self._provisional_intake_application.begin_file(event, original_path)

    @staticmethod
    def _search_terms(text: str, *, natural_language: bool = False) -> str:
        normalized = text.strip()
        for prefix in ("find ", "search ", "look for "):
            if normalized.casefold().startswith(prefix):
                normalized = normalized[len(prefix):].strip()
                break
        if not natural_language:
            return normalized
        words = [
            word for word in normalized.replace("'", "").split()
            if word.casefold() not in {"a", "an", "about", "for", "in", "my", "notes", "on", "the"}
        ]
        return " OR ".join(words) or normalized
