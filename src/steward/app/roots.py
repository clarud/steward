"""Authorized source roots and reviewed same-root move reconciliation."""

from __future__ import annotations

from steward.events import IncomingEvent
from steward.presentation import PresentedReply, ReplyAction
from steward.reviews import ReviewContextRepository
from steward.roots import SourceRootProfileRepository, SourceRootRepository
from steward.sources import SourceMoveProposalRepository, SourceMoveReconciliationService, SourceRepository


class StewardRootsApplication:
    """Report local source-root health without granting Telegram path authority."""

    def __init__(
        self,
        roots: SourceRootRepository,
        *,
        contexts: ReviewContextRepository | None = None,
        profiles: SourceRootProfileRepository | None = None,
    ) -> None:
        self._roots = roots
        self._contexts = contexts
        self._profiles = profiles

    def resolve_root_reference(self, event: IncomingEvent) -> str | PresentedReply | None:
        """Reopen only an explicitly viewed root without exposing its local path.

        Root operations remain a local-only capability. This method is only a
        bounded navigation convenience for a previous Telegram root card.
        """

        if self._contexts is None:
            return None
        normalized = (event.text or "").strip().casefold().rstrip("?!. ")
        if normalized not in {
            "show that root", "open that root", "show the last root", "open the last root",
            "show that source root", "open that source root",
        }:
            return None
        context = self._contexts.get(event.platform, event.chat_id)
        if context is None or context.kind != "root" or not isinstance(context.identifier, int):
            return None
        root = next((item for item in self._roots.list_all() if item.id == context.identifier), None)
        if root is None:
            self._contexts.clear(event.platform, event.chat_id)
            return "That previously opened authorized root is no longer available. Open /roots to continue."
        return self._root_detail(root)

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, _, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/root":
            if not argument.strip().isdigit():
                return "Use /root followed by a numeric root ID."
            root = next((item for item in self._roots.list_all() if item.id == int(argument.strip())), None)
            if root is None:
                return f"Authorized root {argument.strip()} was not found."
            if self._contexts is not None and root.id is not None:
                self._contexts.set(event.platform, event.chat_id, "root", root.id)
            return self._root_detail(root)
        if command != "/roots":
            return None
        if argument.strip() and (not argument.strip().isdigit() or int(argument.strip()) < 1):
            return "Use /roots with an optional positive page number."
        roots = self._roots.list_all()
        if not roots:
            return PresentedReply(
                "No local source roots are authorized yet.\n\n"
                "On the Steward computer, run:\n"
                'steward onboard-root "My Notes" "C:\\path\\to\\notes"\n\n'
                "This indexes the directory in place; original files stay where they are. "
                "Telegram cannot choose or browse local folders.",
                (ReplyAction("Home", "/home"),),
                title="Set up a local source root",
                icon="🗂️",
            )
        pages = max(1, (len(roots) + 7) // 8)
        page = min(max(int(argument.strip()) if argument.strip() else 1, 1), pages)
        visible = roots[(page - 1) * 8:page * 8]
        actions = [ReplyAction(f"Open {index}", f"/root {root.id}") for index, root in enumerate(visible, start=1)]
        if page > 1:
            actions.append(ReplyAction("Previous", f"/roots {page - 1}"))
        if page < pages:
            actions.append(ReplyAction("Next", f"/roots {page + 1}"))
        actions.append(ReplyAction("Home", "/home"))
        return PresentedReply(
            "\n".join(
                [f"Page {page} of {pages}"]
                + [f"{root.id}: {root.name} ({root.health})" for root in visible]
            ),
            tuple(actions),
            title="Authorized source roots",
            icon="🗂️",
        )

    def _root_detail(self, root: object) -> PresentedReply:
        health = str(getattr(root, "health"))
        guidance = (
            "Reconnect or restore this root locally, then scan it locally."
            if health == "missing" else "Enable this root locally before scanning."
            if health == "disabled" else "This root is available for local scans."
        )
        identifier = getattr(root, "id")
        last_scanned_at = getattr(root, "last_scanned_at", None)
        scan_status = last_scanned_at.isoformat() if last_scanned_at is not None else "never"
        counts = getattr(root, "last_scan_counts", None)
        outcome = (
            f"Last scan outcome: new={counts[0]} updated={counts[1]} unchanged={counts[2]} missing={counts[3]}\n"
            if counts is not None else ""
        )
        profile = self._profiles.get(identifier) if self._profiles is not None and isinstance(identifier, int) else None
        profile_details = (
            f"Purpose: {profile.purpose}\n"
            + (f"Authority tiers: {' · '.join(profile.authority_tiers)}\n" if profile.authority_tiers else "")
            if profile is not None else ""
        )
        return PresentedReply(
            f"Status: {health}\n{profile_details}Last successful scan: {scan_status}\n{outcome}Excluded subdirectories: {len(getattr(root, 'exclusions'))}\n\n"
            f"{guidance}\nRoot paths and changes remain local-only.",
            (ReplyAction("Roots", "/roots"), ReplyAction("Home", "/home")),
            title=str(getattr(root, "name")), icon="🗂️",
            reference=("root", identifier) if isinstance(identifier, int) and identifier > 0 else None,
        )


class StewardMoveReconciliationApplication:
    """Expose source-ID-preserving move matches as explicit Telegram reviews."""

    def __init__(self, sources: SourceRepository, proposals: SourceMoveProposalRepository) -> None:
        self._sources = sources
        self._proposals = proposals
        self._service = SourceMoveReconciliationService(sources, proposals)

    def handle_command(self, event: IncomingEvent) -> str | PresentedReply | None:
        command, _, argument = (event.text or "").strip().partition(" ")
        command = command.partition("@")[0]
        if command == "/moves":
            pending = self._proposals.list_pending()
            if not pending:
                return "No unambiguous source moves are waiting for review."
            lines = ["Review each match before preserving its original source ID:"]
            actions: list[ReplyAction] = []
            for proposal in pending[:8]:
                missing = self._sources.get_by_id(proposal.missing_source_id)
                discovered = self._sources.get_by_id(proposal.discovered_source_id)
                if missing is None or discovered is None:
                    continue
                lines.append(f"{proposal.id}. {missing.path.name} → {discovered.path.name}")
                actions.append(ReplyAction(f"Review {proposal.id}", f"/review_move {proposal.id}"))
            return PresentedReply("\n".join(lines), tuple(actions), title="Possible file moves", icon="↔️")
        if command != "/review_move":
            return None
        parts = argument.split()
        if not parts or not parts[0].isdigit():
            return "Use /review_move followed by a numeric move proposal ID."
        proposal = self._proposals.get(int(parts[0]))
        if proposal is None:
            return "That source move proposal was not found."
        if len(parts) == 2 and parts[1] in {"accept", "reject"}:
            try:
                if parts[1] == "accept":
                    source = self._service.accept(proposal.id or 0)
                    return f"Preserved source ID {source.id} at {source.path.name}."
                self._proposals.review(proposal.id or 0, "rejected")
                return f"Kept both histories; move proposal {proposal.id} was rejected."
            except ValueError as error:
                return f"Source move was not changed: {error}"
        missing = self._sources.get_by_id(proposal.missing_source_id)
        discovered = self._sources.get_by_id(proposal.discovered_source_id)
        if missing is None or discovered is None:
            return "This move proposal is no longer valid; run a local root scan again."
        return PresentedReply(
            f"Move proposal {proposal.id}\n\nPrevious source ID {missing.id}: {missing.path.name}\n"
            f"Discovered source ID {discovered.id}: {discovered.path.name}\n\n"
            "Their content hashes match. Accept to preserve the previous source ID; no file will move.",
            (ReplyAction("Preserve source ID", f"/review_move {proposal.id} accept"),
             ReplyAction("Keep separate", f"/review_move {proposal.id} reject"),
             ReplyAction("All moves", "/moves")),
            title="Review source move", icon="↔️",
        )
