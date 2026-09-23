"""Reviewed per-source privacy rules that gate model access."""

from __future__ import annotations

from steward.action_proposals import ActionProposalRepository
from steward.activity import ActivityService, ActivityType
from steward.events import IncomingEvent
from steward.presentation import PresentedReply, ReplyAction
from steward.privacy import PrivacyRule, PrivacyService
from steward.reviews import ReviewContextRepository
from steward.sources import SourceRepository


class StewardPrivacyApplication:
    """Explicit source-level model-boundary controls for an authorized chat."""

    SET_SOURCE_PRIVACY = "set_source_privacy"

    def __init__(
        self,
        privacy: PrivacyService,
        sources: SourceRepository,
        activity: ActivityService | None = None,
        proposals: ActionProposalRepository | None = None,
        contexts: ReviewContextRepository | None = None,
    ) -> None:
        self._privacy = privacy
        self._sources = sources
        self._activity = activity
        self._proposals = proposals
        self._contexts = contexts

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, separator, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/privacy":
            if not separator or not argument.strip().isdigit():
                return "Use /privacy followed by a numeric source ID."
            source_id = int(argument.strip())
            if self._sources.get_by_id(source_id) is None:
                return f"Source {source_id} was not found."
            return f"Source {source_id} privacy rule: {self._privacy.rule_for(source_id).value}"
        if command == "/privacy_options":
            if not separator or not argument.strip().isdigit():
                return "Open a source and choose Privacy, or use /privacy_options followed by a numeric source ID."
            return self._privacy_options(int(argument.strip()))
        if command in {"/approve_action", "/reject_action"}:
            return self._review_privacy_proposal(event, command, separator, argument)
        if command != "/set_privacy":
            return None
        parts = argument.split()
        if not separator or len(parts) != 2 or not parts[0].isdigit():
            return "Use /set_privacy followed by a source ID and privacy rule."
        source_id = int(parts[0])
        if self._sources.get_by_id(source_id) is None:
            return f"Source {source_id} was not found."
        try:
            rule = PrivacyRule(parts[1])
        except ValueError:
            return "Privacy rule must be external_allowed, external_redacted, local_model_only, or no_model."
        previous_rule = self._privacy.rule_for(source_id)
        if self._proposals is not None:
            payload = {"source_id": str(source_id), "rule": rule.value, "chat_id": event.chat_id}
            pending_for_source = next(
                (
                    item for item in self._proposals.list_all()
                    if item.status == "pending"
                    and item.action_type == self.SET_SOURCE_PRIVACY
                    and item.payload.get("source_id") == str(source_id)
                ),
                None,
            )
            if pending_for_source is not None and pending_for_source.payload != payload:
                pending_chat = pending_for_source.payload.get("chat_id")
                if isinstance(pending_chat, str) and pending_chat and pending_chat != event.chat_id:
                    return PresentedReply(
                        f"Source {source_id} already has a privacy review pending in another authorized chat. "
                        "Its rule is unchanged.",
                        (ReplyAction("Home", "/home"),),
                        title="Privacy change pending",
                        icon="🔒",
                    )
                return PresentedReply(
                    f"Source {source_id} already has a pending privacy change to "
                    f"{pending_for_source.payload['rule']}. Review or reject that change before proposing another.",
                    (
                        ReplyAction("Apply pending change", f"/approve_action {pending_for_source.id}"),
                        ReplyAction("Reject pending change", f"/reject_action {pending_for_source.id}"),
                    ),
                    title="Privacy change pending",
                    icon="🔒",
                )
            proposal = pending_for_source or self._proposals.find_pending(self.SET_SOURCE_PRIVACY, payload)
            if proposal is None:
                proposal = self._proposals.add(self.SET_SOURCE_PRIVACY, payload)
                if self._activity is not None:
                    self._activity.record(
                        ActivityType.ACTION_PROPOSED,
                        object_id=str(proposal.id),
                        details=f"Change source {source_id} privacy: {previous_rule.value} -> {rule.value}",
                    )
            consequence = {
                PrivacyRule.EXTERNAL_ALLOWED: "Raw extracted content may be sent to a configured external model.",
                PrivacyRule.EXTERNAL_REDACTED: "External model access remains blocked until Steward has a reviewed redaction capability.",
                PrivacyRule.LOCAL_MODEL_ONLY: "Raw extracted content remains available only to a configured local model.",
                PrivacyRule.NO_MODEL: "No model may receive this source's raw extracted content.",
            }[rule]
            return PresentedReply(
                    f"Source {source_id}: {previous_rule.value} → {rule.value}\n\n{consequence}\n\n"
                "The privacy rule is unchanged until you approve.",
                (
                    ReplyAction("Open source", f"/source {source_id}"),
                    ReplyAction("Apply privacy rule", f"/approve_action {proposal.id}"),
                    ReplyAction("Reject", f"/reject_action {proposal.id}"),
                ),
                title="Review privacy change",
                icon="🔒",
            )
        self._privacy.set_rule(source_id, rule)
        if self._activity is not None and previous_rule != rule:
            self._activity.record(
                ActivityType.SOURCE_PRIVACY_CHANGED,
                object_id=str(source_id),
                details=f"{previous_rule.value} -> {rule.value}",
            )
        return f"Source {source_id} privacy rule set to {rule.value}."

    def _privacy_options(self, source_id: int) -> str | PresentedReply:
        """Show compact, review-required replacement rules for one source."""
        source = self._sources.get_by_id(source_id)
        if source is None:
            return f"Source {source_id} was not found."
        current = self._privacy.rule_for(source_id)
        options = (
            ("Allow cloud", PrivacyRule.EXTERNAL_ALLOWED),
            ("Local only", PrivacyRule.LOCAL_MODEL_ONLY),
            ("No model", PrivacyRule.NO_MODEL),
            ("Block external", PrivacyRule.EXTERNAL_REDACTED),
        )
        actions = tuple(
            ReplyAction(label, f"/set_privacy {source_id} {rule.value}")
            for label, rule in options
            if rule is not current
        ) + (ReplyAction("Back", f"/source {source_id}"),)
        return PresentedReply(
            f"Current rule: {current.value}\n\n"
            "Choose a replacement rule. It remains unchanged until you approve the review.",
            actions,
            title="Source privacy",
            icon="🔒",
            reference=("source", source_id),
        )

    def natural_source_privacy_command(self, event: IncomingEvent) -> str | None:
        """Translate a bounded source-card privacy request into a review.

        A bare conversational phrase cannot choose a source. The source must
        be the exact, durable card context restored for this Telegram chat.
        The returned command is handled by ``handle_command`` and therefore
        follows the same proposal/approval path as the visible picker.
        """

        if self._contexts is None:
            return None
        normalized = (event.text or "").strip().casefold().rstrip("?!. ")
        rules = {
            "keep this local": PrivacyRule.LOCAL_MODEL_ONLY,
            "keep that local": PrivacyRule.LOCAL_MODEL_ONLY,
            "use local model only": PrivacyRule.LOCAL_MODEL_ONLY,
            "do not send this to the cloud": PrivacyRule.LOCAL_MODEL_ONLY,
            "don't send this to the cloud": PrivacyRule.LOCAL_MODEL_ONLY,
            "allow cloud for this source": PrivacyRule.EXTERNAL_ALLOWED,
            "allow cloud for that source": PrivacyRule.EXTERNAL_ALLOWED,
            "allow a cloud model": PrivacyRule.EXTERNAL_ALLOWED,
            "do not use a model for this": PrivacyRule.NO_MODEL,
            "don't use a model for this": PrivacyRule.NO_MODEL,
            "no model for this": PrivacyRule.NO_MODEL,
        }
        rule = rules.get(normalized)
        if rule is None:
            return None
        context = self._contexts.get(event.platform, event.chat_id)
        if context is None or context.kind != "source":
            return None
        source_id = int(context.identifier)
        if self._sources.get_by_id(source_id) is None:
            self._contexts.clear(event.platform, event.chat_id)
            return None
        return f"/set_privacy {source_id} {rule.value}"

    def _review_privacy_proposal(
        self, event: IncomingEvent, command: str, separator: str, argument: str
    ) -> str | PresentedReply | None:
        if self._proposals is None:
            return None
        if not separator or not argument.strip().isdigit():
            return None
        proposal = self._proposals.get(int(argument.strip()))
        if proposal is None or proposal.action_type != self.SET_SOURCE_PRIVACY:
            return None
        decision = "accepted" if command == "/approve_action" else "rejected"
        if proposal.status == decision:
            return f"Privacy proposal {proposal.id} was already {proposal.status}."
        if proposal.status != "pending":
            return f"Privacy proposal {proposal.id} was already {proposal.status}."
        proposal_chat = proposal.payload.get("chat_id")
        if not isinstance(proposal_chat, str) or not proposal_chat:
            if command == "/reject_action":
                self._proposals.set_status(proposal.id or 0, "rejected")
                if self._activity is not None:
                    self._activity.record(
                        ActivityType.ACTION_REJECTED,
                        object_id=str(proposal.id),
                        details="Legacy unbound privacy proposal declined",
                    )
                return "Legacy privacy proposal declined. Create a fresh review from the source card."
            return "This older privacy review is missing its chat binding. Reject it, then create a fresh review from the source card."
        if proposal_chat != event.chat_id:
            return "This privacy review belongs to another authorized Telegram chat. The source rule was not changed."
        source_id = int(proposal.payload["source_id"])
        source = self._sources.get_by_id(source_id)
        if source is None:
            return "The source for this privacy proposal is no longer registered. The proposal remains pending."
        if decision == "accepted":
            rule = PrivacyRule(proposal.payload["rule"])
            previous_rule = self._privacy.rule_for(source_id)
            self._privacy.set_rule(source_id, rule)
            if self._activity is not None and previous_rule != rule:
                self._activity.record(
                    ActivityType.SOURCE_PRIVACY_CHANGED,
                    object_id=str(source_id),
                    details=f"{previous_rule.value} -> {rule.value}",
                )
            text = f"Source {source_id} privacy rule is now {rule.value}."
            title = "Privacy rule applied"
            icon = "🔒"
        else:
            text = f"Source {source_id} privacy rule remains {self._privacy.rule_for(source_id).value}."
            title = "Privacy change declined"
            icon = "↩️"
        self._proposals.set_status(proposal.id or 0, decision)
        if self._activity is not None:
            self._activity.record(
                ActivityType.ACTION_ACCEPTED if decision == "accepted" else ActivityType.ACTION_REJECTED,
                object_id=str(proposal.id),
                details=proposal.action_type,
            )
        if self._contexts is not None:
            self._contexts.set(event.platform, event.chat_id, "source", source_id)
        return PresentedReply(
            text,
            (
                ReplyAction("Open source", f"/source {source_id}"),
                ReplyAction("Privacy options", f"/privacy_options {source_id}"),
                ReplyAction("Home", "/home"),
            ),
            title=title,
            icon=icon,
            reference=("source", source_id),
        )
