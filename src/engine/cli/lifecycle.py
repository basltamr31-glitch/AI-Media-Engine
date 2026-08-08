"""What happens to content after a model writes it.

The CLI is the application layer (ADR 0002), so these use cases live here
rather than in a service layer that would otherwise hold five functions. They
are kept out of the command modules because they are the part worth testing:
the Typer wrappers add argument parsing and a table, and nothing else.

Three rules hold across all of them, and each one exists because the opposite
loses something that cannot be recovered afterwards:

1. **Every action appends, never overwrites.** A rejection does not delete the
   draft, an edit does not replace the generated text, and a correction to a
   metric is a second observation rather than an update.
2. **Every action is attributed to a real actor.** Human decisions attribute to
   a human; only the pipeline uses the system actor. The machine-versus-human
   distinction is the training signal, and it is destroyed at the point of
   capture if the attribution is wrong.
3. **Every action emits a signal.** The state machine records where content
   *is*; the signal log records *what happened to it and why*, which is the
   part a learner can consume.

These are deliberately not pipeline stages. A stage is unattended batch work
over everything eligible; a review decision is one human, one asset, one
judgement -- and modelling it as a batch would mean the reason for the decision
had nowhere to live.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWork
from engine.core.domain.account import Actor
from engine.core.domain.content import (
    ContentAsset,
    ContentRevision,
    PerformanceMetric,
    Publication,
    RevisionOrigin,
)
from engine.core.domain.editing import EditDelta, measure_edit
from engine.core.domain.enums import ContentState, SignalPolarity, SubjectType
from engine.core.domain.signal import (
    SIGNAL_CONTENT_APPROVED,
    SIGNAL_CONTENT_EDITED,
    SIGNAL_CONTENT_PUBLISHED,
    SIGNAL_CONTENT_REJECTED,
    SIGNAL_ENGAGEMENT,
    Signal,
)
from engine.core.ports.clock import Clock


class LifecycleError(RuntimeError):
    """A review action that the content's current state does not permit.

    Separate from ``ValueError`` so the CLI can report it as a user error with
    an exit code rather than as a traceback: being told "that post is already
    published" is not a crash.
    """


@dataclass(frozen=True)
class EditOutcome:
    """What an edit did, including the part the caller cannot infer.

    ``returned_to_draft`` is reported rather than deduced from the resulting
    state: an asset that was already a draft ends in the same state as one that
    was pushed back out of approval, and only one of those is worth telling the
    operator about.
    """

    asset: ContentAsset
    revision: ContentRevision
    delta: EditDelta
    returned_to_draft: bool


@dataclass(frozen=True)
class ReviewItem:
    """One asset as the review queue shows it.

    Assembled here rather than queried as a join because the queue is small by
    construction -- it is a day's output awaiting a human -- and a read model
    the CLI can render without a second lookup keeps the command layer thin.
    """

    asset: ContentAsset
    revision: ContentRevision | None
    headline: str

    @property
    def body(self) -> str:
        return self.revision.body if self.revision else ""

    @property
    def characters(self) -> int:
        return len(self.body)

    @property
    def was_edited(self) -> bool:
        """Whether a human has already rewritten this draft."""
        return self.revision is not None and self.revision.origin is RevisionOrigin.EDITED


def queue(
    uow: SqlAlchemyUnitOfWork,
    workspace_id: str,
    *,
    state: ContentState | None = None,
    limit: int = 50,
) -> list[ReviewItem]:
    """Content awaiting a decision, newest first.

    Defaults to everything still under consideration -- draft and in-review --
    because "what is waiting for me?" is the question the queue exists to
    answer, and an operator who wanted the published archive would ask for it.
    """
    wanted = {state} if state is not None else {ContentState.DRAFT, ContentState.IN_REVIEW}
    assets = [
        asset
        for asset in uow.assets.list_recent(workspace_id, limit=limit)
        if asset.state in wanted
    ]
    return [_review_item(uow, workspace_id, asset) for asset in assets]


def show(uow: SqlAlchemyUnitOfWork, workspace_id: str, asset_id: str) -> ReviewItem:
    """One asset with its current text.

    Raises:
        LifecycleError: If no such asset exists in this workspace.
    """
    return _review_item(uow, workspace_id, _asset(uow, workspace_id, asset_id))


def approve(
    uow: SqlAlchemyUnitOfWork,
    workspace_id: str,
    asset_id: str,
    *,
    actor: Actor,
    clock: Clock,
    notes: str | None = None,
) -> ContentAsset:
    """Clear an asset for publication.

    A draft is walked through ``in_review`` on the way, rather than being
    allowed to jump the gate: the state machine's job is to make "published
    without review" unrepresentable, and a shortcut that skips a state is the
    same hole with a friendlier name.

    Raises:
        LifecycleError: If the asset is already published, archived, or
            otherwise cannot reach ``approved`` from where it is.
    """
    asset = _asset(uow, workspace_id, asset_id)
    _walk_to_review(asset, action="approve")
    _transition(asset, ContentState.APPROVED, notes=notes, action="approve")
    uow.assets.update(asset)

    revision = uow.assets.latest_revision(workspace_id, asset.id)
    _emit(
        uow,
        asset=asset,
        actor=actor,
        clock=clock,
        signal_type=SIGNAL_CONTENT_APPROVED,
        polarity=SignalPolarity.POSITIVE,
        label=notes,
        context={
            "channel_key": asset.channel_key or "",
            "language": str(asset.language),
            "brand_profile_key": asset.brand_profile_key,
            # Whether the approved text is the model's or a rewrite. Approval
            # of untouched output is the strongest evidence the generator is
            # working; approval after a rewrite says the opposite.
            "revision_origin": revision.origin.value if revision else "",
            "revision_number": revision.revision_number if revision else 0,
        },
    )
    return asset


def reject(
    uow: SqlAlchemyUnitOfWork,
    workspace_id: str,
    asset_id: str,
    *,
    actor: Actor,
    clock: Clock,
    reason: str,
) -> ContentAsset:
    """Refuse an asset, recording why.

    The reason is required rather than optional. A rejection with no reason
    records that a human disliked something and nothing about what, which is
    the one shape of feedback that cannot be learned from -- and this log is
    being kept precisely so it can be.

    Raises:
        LifecycleError: If the reason is blank, or the asset cannot be
            rejected from its current state.
    """
    if not reason.strip():
        msg = "a rejection needs a reason; 'no' on its own teaches the engine nothing"
        raise LifecycleError(msg)

    asset = _asset(uow, workspace_id, asset_id)
    _walk_to_review(asset, action="reject")
    _transition(asset, ContentState.REJECTED, notes=reason, action="reject")
    uow.assets.update(asset)

    _emit(
        uow,
        asset=asset,
        actor=actor,
        clock=clock,
        signal_type=SIGNAL_CONTENT_REJECTED,
        polarity=SignalPolarity.NEGATIVE,
        label=reason.strip(),
        context={
            "channel_key": asset.channel_key or "",
            "language": str(asset.language),
            "brand_profile_key": asset.brand_profile_key,
        },
    )
    return asset


def edit(
    uow: SqlAlchemyUnitOfWork,
    workspace_id: str,
    asset_id: str,
    *,
    actor: Actor,
    clock: Clock,
    body: str,
) -> EditOutcome:
    """Replace an asset's text with a human's, keeping both.

    An approved asset returns to ``draft``, because an approval refers to the
    text that was approved: leaving it approved would mean the gate had been
    passed by words nobody read. A published asset is refused outright -- the
    post is already live, and editing the record of it would make the archive
    disagree with the world.

    Returns:
        The asset, the appended revision, the measured difference, and
        whether the edit cost the asset its approval.

    Raises:
        LifecycleError: If the asset is published or archived, or the body is
            blank.
    """
    if not body.strip():
        msg = "an edit needs a body; use reject to refuse the asset instead"
        raise LifecycleError(msg)

    asset = _asset(uow, workspace_id, asset_id)
    if asset.state in {ContentState.PUBLISHED, ContentState.ARCHIVED}:
        msg = (
            f"asset {asset.id} is {asset.state.value} and cannot be edited; "
            "publish a correction as new content instead"
        )
        raise LifecycleError(msg)

    previous = uow.assets.latest_revision(workspace_id, asset.id)
    delta = measure_edit(previous.body if previous else "", body)

    revision = asset.next_revision(
        body,
        origin=RevisionOrigin.EDITED,
        actor_id=actor.id,
        previous=previous,
        meta=dict(delta.as_context()),
    )
    uow.assets.add_revision(revision)
    asset.current_revision_id = revision.id
    returned_to_draft = asset.state is ContentState.APPROVED
    if returned_to_draft:
        _transition(asset, ContentState.DRAFT, action="edit")
    asset.updated_at = clock.now()
    uow.assets.update(asset)

    _emit(
        uow,
        asset=asset,
        actor=actor,
        clock=clock,
        signal_type=SIGNAL_CONTENT_EDITED,
        # Negative: a draft that needed rewriting is evidence against the
        # generator, whatever the editor thought of the result.
        polarity=SignalPolarity.NEGATIVE,
        # The headline number. `1 - similarity` so larger means more work was
        # needed, which is the direction every other cost in this system runs.
        value=round(1.0 - delta.similarity, 4),
        context={
            "channel_key": asset.channel_key or "",
            "language": str(asset.language),
            "brand_profile_key": asset.brand_profile_key,
            "revision_number": revision.revision_number,
            "prompt_version": previous.prompt_version if previous else "",
            "model": previous.model if previous else "",
            **delta.as_context(),
        },
    )
    return EditOutcome(
        asset=asset, revision=revision, delta=delta, returned_to_draft=returned_to_draft
    )


def record_publication(
    uow: SqlAlchemyUnitOfWork,
    workspace_id: str,
    asset_id: str,
    *,
    actor: Actor,
    clock: Clock,
    external_url: str | None = None,
    external_id: str | None = None,
    published_at: datetime | None = None,
) -> Publication:
    """Record that an approved asset went live, and where.

    The engine records publication; it does not perform it (ADR 0018). This is
    a human reporting a fact, which is why ``published_at`` can be backdated --
    posts get recorded after the fact, and stamping them with the moment of
    data entry would corrupt every timing analysis the log exists to support.

    Raises:
        LifecycleError: If the asset is not approved, or is already recorded as
            published on this channel.
    """
    asset = _asset(uow, workspace_id, asset_id)
    if asset.state is not ContentState.APPROVED:
        msg = (
            f"asset {asset.id} is {asset.state.value}, not approved; "
            "nothing reaches a channel without passing review"
        )
        raise LifecycleError(msg)

    channel_key = asset.channel_key or ""
    if not channel_key:
        msg = f"asset {asset.id} has no channel, so there is nowhere for it to have gone live"
        raise LifecycleError(msg)

    # No duplicate check here on purpose. ``published`` leads only to
    # ``archived``, so an asset can never be approved a second time and reach
    # this line twice; a check for it would be unreachable code that invites
    # the reader to believe it is what protects them. The real backstop is the
    # unique constraint on (asset_id, channel_key), which also holds against
    # anything that bypasses this function.
    when = _past_moment(published_at, clock=clock, label="publication time")
    publication = Publication(
        workspace_id=workspace_id,
        asset_id=asset.id,
        channel_key=channel_key,
        external_url=external_url,
        external_id=external_id,
        published_at=when,
    )
    uow.publications.add(publication)

    _transition(asset, ContentState.PUBLISHED, action="publish")
    uow.assets.update(asset)

    _emit(
        uow,
        asset=asset,
        actor=actor,
        clock=clock,
        signal_type=SIGNAL_CONTENT_PUBLISHED,
        polarity=SignalPolarity.POSITIVE,
        occurred_at=when,
        context={
            "publication_id": publication.id,
            "channel_key": channel_key,
            "language": str(asset.language),
            "brand_profile_key": asset.brand_profile_key,
            "story_id": asset.story_id,
        },
    )
    return publication


def record_metric(
    uow: SqlAlchemyUnitOfWork,
    workspace_id: str,
    publication_id: str,
    *,
    actor: Actor,
    clock: Clock,
    metric: str,
    value: float,
    captured_at: datetime | None = None,
) -> PerformanceMetric:
    """Record one engagement observation, typed in by hand.

    Hand entry is the honest V1: platform APIs need per-network OAuth and each
    one breaks differently, whereas a number typed in after a week still closes
    the loop. The schema does not know or care which it was -- ``collected_by``
    is the only difference, so automating collection later changes a caller and
    nothing else.

    Raises:
        LifecycleError: If the publication does not exist in this workspace.
    """
    publication = uow.publications.get(workspace_id, publication_id)
    if publication is None:
        msg = f"publication {publication_id} not found in this workspace"
        raise LifecycleError(msg)

    when = _past_moment(captured_at, clock=clock, label="capture time")
    observation = PerformanceMetric(
        workspace_id=workspace_id,
        publication_id=publication.id,
        metric=metric,
        value=value,
        captured_at=when,
    )
    uow.metrics.add(observation)

    asset = uow.assets.get(workspace_id, publication.asset_id)
    _emit_signal(
        uow,
        workspace_id=workspace_id,
        account_id=actor.account_id,
        actor=actor,
        subject_type=SubjectType.PUBLICATION,
        subject_id=publication.id,
        signal_type=SIGNAL_ENGAGEMENT,
        polarity=SignalPolarity.NEUTRAL,
        value=value,
        label=observation.metric,
        context={
            "metric": observation.metric,
            "channel_key": publication.channel_key,
            "asset_id": publication.asset_id,
            "story_id": asset.story_id if asset else "",
            "language": str(asset.language) if asset else "",
        },
        # The observation is *about* the moment the post was measured, and we
        # learned of it now. Collapsing the two would make every engagement
        # reading look instantaneous and destroy the timing analysis.
        occurred_at=when,
        clock=clock,
    )
    return observation


# --- internals ---------------------------------------------------------------


def _past_moment(when: datetime | None, *, clock: Clock, label: str) -> datetime:
    """Resolve a caller-supplied timestamp, refusing the future.

    Backdating is the normal case -- both publication and engagement get
    recorded after the fact. A time *ahead* of now is a different thing: it
    describes an observation nobody could have made, and the signal log rejects
    it downstream with "recorded_at cannot precede occurred_at", which is a
    true statement that explains nothing to whoever mistyped the date.
    """
    now = clock.now()
    if when is None:
        return now
    if when > now:
        msg = f"{label} {when.isoformat()} is in the future; it has not happened yet"
        raise LifecycleError(msg)
    return when


def _asset(uow: SqlAlchemyUnitOfWork, workspace_id: str, asset_id: str) -> ContentAsset:
    asset = uow.assets.get(workspace_id, asset_id)
    if asset is None:
        msg = f"content asset {asset_id} not found in this workspace"
        raise LifecycleError(msg)
    return asset


def _review_item(uow: SqlAlchemyUnitOfWork, workspace_id: str, asset: ContentAsset) -> ReviewItem:
    revision = (
        uow.assets.get_revision(workspace_id, asset.current_revision_id)
        if asset.current_revision_id
        else None
    )
    story = uow.stories.get(workspace_id, asset.story_id)
    return ReviewItem(
        asset=asset,
        revision=revision,
        headline=story.canonical_headline if story else "",
    )


def _walk_to_review(asset: ContentAsset, *, action: str) -> None:
    """Move a draft to in-review so a decision has a gate to pass through."""
    if asset.state is ContentState.DRAFT:
        _transition(asset, ContentState.IN_REVIEW, action=action)


def _transition(
    asset: ContentAsset,
    target: ContentState,
    *,
    action: str,
    notes: str | None = None,
) -> None:
    """Apply a state change, restating the domain's refusal as a user error."""
    try:
        asset.transition_to(target, notes=notes)
    except ValueError as error:
        msg = f"cannot {action} asset {asset.id}: {error}"
        raise LifecycleError(msg) from error


def _emit(
    uow: SqlAlchemyUnitOfWork,
    *,
    asset: ContentAsset,
    actor: Actor,
    clock: Clock,
    signal_type: str,
    polarity: SignalPolarity,
    value: float | None = None,
    label: str | None = None,
    context: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> Signal:
    """Emit a signal about a content asset."""
    return _emit_signal(
        uow,
        workspace_id=asset.workspace_id,
        account_id=actor.account_id,
        actor=actor,
        subject_type=SubjectType.CONTENT_ASSET,
        subject_id=asset.id,
        signal_type=signal_type,
        polarity=polarity,
        value=value,
        label=label,
        context=context or {},
        occurred_at=occurred_at,
        clock=clock,
    )


def _emit_signal(
    uow: SqlAlchemyUnitOfWork,
    *,
    workspace_id: str,
    account_id: str,
    actor: Actor,
    subject_type: SubjectType,
    subject_id: str,
    signal_type: str,
    polarity: SignalPolarity,
    value: float | None,
    label: str | None,
    context: dict[str, object],
    occurred_at: datetime | None,
    clock: Clock,
) -> Signal:
    now = clock.now()
    signal = Signal(
        account_id=account_id,
        workspace_id=workspace_id,
        actor_id=actor.id,
        subject_type=subject_type,
        subject_id=subject_id,
        signal_type=signal_type,
        polarity=polarity,
        value=value,
        label=label,
        context=context,
        occurred_at=occurred_at or now,
        recorded_at=now,
    )
    uow.signals.emit(signal)
    return signal
