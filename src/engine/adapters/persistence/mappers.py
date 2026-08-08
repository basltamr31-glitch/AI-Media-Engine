"""Translation between domain models and ORM rows.

Mapping is explicit and hand-written rather than using ORM-backed domain
objects. The cost is a little boilerplate; the benefits are that the domain
stays free of SQLAlchemy, entities cannot lazily trigger database I/O from
inside business logic, and the storage schema can evolve independently of the
domain shape.
"""

from __future__ import annotations

from engine.adapters.persistence.models import (
    AccountRow,
    ActorRow,
    ContentAssetRow,
    ContentRevisionRow,
    EditorialScoreRow,
    EvidenceRow,
    MembershipRow,
    PerformanceMetricRow,
    PublicationRow,
    RunRow,
    RunStageRow,
    SignalRow,
    SourceDocumentRow,
    SourceRow,
    StoryCandidateRow,
    StoryRow,
    WorkspaceRow,
)
from engine.core.domain.account import Account, Actor, Membership, Quota
from engine.core.domain.content import (
    ContentAsset,
    ContentRevision,
    PerformanceMetric,
    Publication,
    RevisionOrigin,
)
from engine.core.domain.enums import (
    ActorKind,
    ContentState,
    EvidenceKind,
    MembershipRole,
    PlanTier,
    RunStatus,
    SignalPolarity,
    SourceKind,
    StageStatus,
    StoryStatus,
    SubjectType,
)
from engine.core.domain.language import LanguageTag
from engine.core.domain.ledger import RunLedger, StageRecord, TokenUsage
from engine.core.domain.matching import title_key, url_key
from engine.core.domain.signal import Signal
from engine.core.domain.source import Source, SourceDocument
from engine.core.domain.story import (
    EditorialScore,
    EnrichedSource,
    Evidence,
    Story,
    StoryCandidate,
)
from engine.core.domain.workspace import Workspace

# --- Account, Actor, Membership ----------------------------------------------


def account_to_row(account: Account) -> AccountRow:
    return AccountRow(
        id=account.id,
        slug=account.slug,
        name=account.name,
        plan=account.plan.value,
        quota=account.quota.model_dump(),
        is_active=account.is_active,
        created_at=account.created_at,
        updated_at=account.updated_at,
        meta=account.meta,
    )


def account_from_row(row: AccountRow) -> Account:
    return Account(
        id=row.id,
        slug=row.slug,
        name=row.name,
        plan=PlanTier(row.plan),
        quota=Quota(**row.quota) if row.quota else Quota(),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        meta=dict(row.meta),
    )


def apply_account(row: AccountRow, account: Account) -> None:
    row.slug = account.slug
    row.name = account.name
    row.plan = account.plan.value
    row.quota = account.quota.model_dump()
    row.is_active = account.is_active
    row.updated_at = account.updated_at
    row.meta = account.meta


def actor_to_row(actor: Actor) -> ActorRow:
    return ActorRow(
        id=actor.id,
        account_id=actor.account_id,
        key=actor.key,
        display_name=actor.display_name,
        kind=actor.kind.value,
        email=actor.email,
        is_active=actor.is_active,
        created_at=actor.created_at,
        meta=actor.meta,
    )


def actor_from_row(row: ActorRow) -> Actor:
    return Actor(
        id=row.id,
        account_id=row.account_id,
        key=row.key,
        display_name=row.display_name,
        kind=ActorKind(row.kind),
        email=row.email,
        is_active=row.is_active,
        created_at=row.created_at,
        meta=dict(row.meta),
    )


def membership_to_row(membership: Membership) -> MembershipRow:
    return MembershipRow(
        id=membership.id,
        account_id=membership.account_id,
        actor_id=membership.actor_id,
        role=membership.role.value,
        created_at=membership.created_at,
    )


def membership_from_row(row: MembershipRow) -> Membership:
    return Membership(
        id=row.id,
        account_id=row.account_id,
        actor_id=row.actor_id,
        role=MembershipRole(row.role),
        created_at=row.created_at,
    )


# --- Signal ------------------------------------------------------------------
#
# No `apply_signal`. Signals are append-only (ADR 0012); the absence of an update
# mapper is the enforcement mechanism, not an oversight.


def signal_to_row(signal: Signal) -> SignalRow:
    return SignalRow(
        id=signal.id,
        account_id=signal.account_id,
        workspace_id=signal.workspace_id,
        actor_id=signal.actor_id,
        subject_type=signal.subject_type.value,
        subject_id=signal.subject_id,
        signal_type=signal.signal_type,
        polarity=signal.polarity.value,
        value=signal.value,
        label=signal.label,
        context=signal.context,
        run_id=signal.run_id,
        occurred_at=signal.occurred_at,
        recorded_at=signal.recorded_at,
    )


def signal_from_row(row: SignalRow) -> Signal:
    return Signal(
        id=row.id,
        account_id=row.account_id,
        workspace_id=row.workspace_id,
        actor_id=row.actor_id,
        subject_type=SubjectType(row.subject_type),
        subject_id=row.subject_id,
        signal_type=row.signal_type,
        polarity=SignalPolarity(row.polarity),
        value=row.value,
        label=row.label,
        context=dict(row.context),
        run_id=row.run_id,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
    )


# --- Workspace ---------------------------------------------------------------


def workspace_to_row(workspace: Workspace) -> WorkspaceRow:
    return WorkspaceRow(
        id=workspace.id,
        account_id=workspace.account_id,
        slug=workspace.slug,
        name=workspace.name,
        pack_key=workspace.pack_key,
        timezone=workspace.timezone,
        languages=list(workspace.languages),
        is_active=workspace.is_active,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        meta=workspace.meta,
    )


def workspace_from_row(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        account_id=row.account_id,
        slug=row.slug,
        name=row.name,
        pack_key=row.pack_key,
        timezone=row.timezone,
        languages=list(row.languages),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        meta=dict(row.meta),
    )


def apply_workspace(row: WorkspaceRow, workspace: Workspace) -> None:
    """Copy mutable fields onto an attached row for an update."""
    row.slug = workspace.slug
    row.name = workspace.name
    row.timezone = workspace.timezone
    row.languages = list(workspace.languages)
    row.is_active = workspace.is_active
    row.updated_at = workspace.updated_at
    row.meta = workspace.meta


# --- Source ------------------------------------------------------------------


def source_to_row(source: Source) -> SourceRow:
    return SourceRow(
        id=source.id,
        workspace_id=source.workspace_id,
        kind=source.kind.value,
        name=source.name,
        identifier=source.identifier,
        parser_key=source.parser_key,
        trust_weight=source.trust_weight,
        is_active=source.is_active,
        config=source.config,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def source_from_row(row: SourceRow) -> Source:
    return Source(
        id=row.id,
        workspace_id=row.workspace_id,
        kind=SourceKind(row.kind),
        name=row.name,
        identifier=row.identifier,
        parser_key=row.parser_key,
        trust_weight=row.trust_weight,
        is_active=row.is_active,
        config=dict(row.config),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def apply_source(row: SourceRow, source: Source) -> None:
    row.kind = source.kind.value
    row.name = source.name
    row.identifier = source.identifier
    row.parser_key = source.parser_key
    row.trust_weight = source.trust_weight
    row.is_active = source.is_active
    row.config = source.config
    row.updated_at = source.updated_at


# --- SourceDocument ----------------------------------------------------------


def document_to_row(document: SourceDocument) -> SourceDocumentRow:
    return SourceDocumentRow(
        id=document.id,
        workspace_id=document.workspace_id,
        source_id=document.source_id,
        external_id=document.external_id,
        content_hash=document.content_hash,
        title=document.title,
        author=document.author,
        url=document.url,
        content_type=document.content_type,
        raw_content=document.raw_content,
        published_at=document.published_at,
        fetched_at=document.fetched_at,
        meta=document.meta,
    )


def document_from_row(row: SourceDocumentRow) -> SourceDocument:
    return SourceDocument(
        id=row.id,
        workspace_id=row.workspace_id,
        source_id=row.source_id,
        external_id=row.external_id,
        content_hash=row.content_hash,
        title=row.title,
        author=row.author,
        url=row.url,
        content_type=row.content_type,
        raw_content=row.raw_content,
        published_at=row.published_at,
        fetched_at=row.fetched_at,
        meta=dict(row.meta),
    )


def candidate_to_row(candidate: StoryCandidate) -> StoryCandidateRow:
    return StoryCandidateRow(
        id=candidate.id,
        workspace_id=candidate.workspace_id,
        document_id=candidate.document_id,
        source_id=candidate.source_id,
        story_id=candidate.story_id,
        headline=candidate.headline,
        summary=candidate.summary,
        primary_url=candidate.primary_url,
        raw_excerpt=candidate.raw_excerpt,
        ordinal=candidate.ordinal,
        extracted_at=candidate.extracted_at,
        meta=candidate.meta,
    )


def candidate_from_row(row: StoryCandidateRow) -> StoryCandidate:
    return StoryCandidate(
        id=row.id,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        source_id=row.source_id,
        story_id=row.story_id,
        headline=row.headline,
        summary=row.summary,
        primary_url=row.primary_url,
        raw_excerpt=row.raw_excerpt,
        ordinal=row.ordinal,
        extracted_at=row.extracted_at,
        meta=dict(row.meta),
    )


def story_to_row(story: Story) -> StoryRow:
    return StoryRow(
        id=story.id,
        workspace_id=story.workspace_id,
        canonical_headline=story.canonical_headline,
        canonical_summary=story.canonical_summary,
        primary_url=story.primary_url,
        url_key=url_key(story.primary_url),
        title_key=title_key(story.canonical_headline),
        status=story.status.value,
        topics=story.topics,
        entities=story.entities,
        enrichment=story.enrichment.model_dump(mode="json") if story.enrichment else None,
        selected_at=story.selected_at,
        first_seen_at=story.first_seen_at,
        last_seen_at=story.last_seen_at,
        meta=story.meta,
    )


def story_from_row(
    row: StoryRow,
    *,
    candidate_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> Story:
    """Rebuild a story.

    ``candidate_ids`` and ``source_ids`` are not columns: they are derived from
    the candidates pointing at this story, so the repository supplies them from
    one grouped query rather than the row duplicating -- and eventually
    contradicting -- the foreign key.
    """
    return Story(
        id=row.id,
        workspace_id=row.workspace_id,
        canonical_headline=row.canonical_headline,
        canonical_summary=row.canonical_summary,
        primary_url=row.primary_url,
        status=StoryStatus(row.status),
        candidate_ids=list(candidate_ids or []),
        source_ids=list(source_ids or []),
        topics=list(row.topics),
        entities=list(row.entities),
        enrichment=EnrichedSource(**row.enrichment) if row.enrichment else None,
        selected_at=row.selected_at,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        meta=dict(row.meta),
    )


def apply_story(row: StoryRow, story: Story) -> None:
    row.canonical_headline = story.canonical_headline
    row.canonical_summary = story.canonical_summary
    row.primary_url = story.primary_url
    row.url_key = url_key(story.primary_url)
    row.title_key = title_key(story.canonical_headline)
    row.status = story.status.value
    row.topics = story.topics
    row.entities = story.entities
    row.enrichment = story.enrichment.model_dump(mode="json") if story.enrichment else None
    row.selected_at = story.selected_at
    row.first_seen_at = story.first_seen_at
    row.last_seen_at = story.last_seen_at
    row.meta = story.meta


def evidence_to_row(evidence: Evidence) -> EvidenceRow:
    return EvidenceRow(
        id=evidence.id,
        workspace_id=evidence.workspace_id,
        story_id=evidence.story_id,
        claim=evidence.claim,
        kind=evidence.kind.value,
        source_url=evidence.source_url,
        source_title=evidence.source_title,
        quote=evidence.quote,
        captured_at=evidence.captured_at,
    )


def evidence_from_row(row: EvidenceRow) -> Evidence:
    return Evidence(
        id=row.id,
        workspace_id=row.workspace_id,
        story_id=row.story_id,
        claim=row.claim,
        kind=EvidenceKind(row.kind),
        source_url=row.source_url,
        source_title=row.source_title,
        quote=row.quote,
        captured_at=row.captured_at,
    )


def score_to_row(score: EditorialScore) -> EditorialScoreRow:
    return EditorialScoreRow(
        id=score.id,
        workspace_id=score.workspace_id,
        story_id=score.story_id,
        run_id=score.run_id,
        total=score.total,
        components=score.components,
        raw=score.raw,
        rationale=score.rationale,
        scored_at=score.scored_at,
    )


def score_from_row(row: EditorialScoreRow) -> EditorialScore:
    return EditorialScore(
        id=row.id,
        workspace_id=row.workspace_id,
        story_id=row.story_id,
        run_id=row.run_id,
        total=row.total,
        components={key: float(value) for key, value in row.components.items()},
        raw={key: float(value) for key, value in row.raw.items()},
        rationale=row.rationale,
        scored_at=row.scored_at,
    )


# --- Content -----------------------------------------------------------------


def asset_to_row(asset: ContentAsset) -> ContentAssetRow:
    return ContentAssetRow(
        id=asset.id,
        workspace_id=asset.workspace_id,
        story_id=asset.story_id,
        run_id=asset.run_id,
        product_key=asset.product_key,
        channel_key=asset.channel_key,
        language=str(asset.language),
        brand_profile_key=asset.brand_profile_key,
        current_revision_id=asset.current_revision_id,
        state=asset.state.value,
        variant=asset.variant,
        evidence_ids=list(asset.evidence_ids),
        review_notes=asset.review_notes,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        meta=asset.meta,
    )


def asset_from_row(row: ContentAssetRow) -> ContentAsset:
    return ContentAsset(
        id=row.id,
        workspace_id=row.workspace_id,
        story_id=row.story_id,
        run_id=row.run_id,
        product_key=row.product_key,
        channel_key=row.channel_key,
        language=LanguageTag(row.language),
        brand_profile_key=row.brand_profile_key,
        current_revision_id=row.current_revision_id,
        state=ContentState(row.state),
        variant=row.variant,
        evidence_ids=list(row.evidence_ids),
        review_notes=row.review_notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
        meta=dict(row.meta),
    )


def apply_asset(row: ContentAssetRow, asset: ContentAsset) -> None:
    """Copy mutable state onto an existing row.

    Identity, story and slot are omitted deliberately: changing any of them
    would make this a different asset, and the unique constraint on the slot
    exists to say so.
    """
    row.current_revision_id = asset.current_revision_id
    row.state = asset.state.value
    row.evidence_ids = list(asset.evidence_ids)
    row.review_notes = asset.review_notes
    row.updated_at = asset.updated_at
    row.meta = asset.meta


def revision_to_row(revision: ContentRevision) -> ContentRevisionRow:
    return ContentRevisionRow(
        id=revision.id,
        workspace_id=revision.workspace_id,
        asset_id=revision.asset_id,
        revision_number=revision.revision_number,
        body=revision.body,
        origin=revision.origin.value,
        actor_id=revision.actor_id,
        parent_revision_id=revision.parent_revision_id,
        prompt_version=revision.prompt_version,
        model=revision.model,
        input_tokens=revision.usage.input_tokens,
        output_tokens=revision.usage.output_tokens,
        cache_read_tokens=revision.usage.cache_read_tokens,
        cache_write_tokens=revision.usage.cache_write_tokens,
        cost_usd=revision.cost_usd,
        created_at=revision.created_at,
        meta=revision.meta,
    )


def revision_from_row(row: ContentRevisionRow) -> ContentRevision:
    return ContentRevision(
        id=row.id,
        workspace_id=row.workspace_id,
        asset_id=row.asset_id,
        revision_number=row.revision_number,
        body=row.body,
        origin=RevisionOrigin(row.origin),
        actor_id=row.actor_id,
        parent_revision_id=row.parent_revision_id,
        prompt_version=row.prompt_version,
        model=row.model,
        usage=TokenUsage(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_read_tokens=row.cache_read_tokens,
            cache_write_tokens=row.cache_write_tokens,
        ),
        cost_usd=row.cost_usd,
        created_at=row.created_at,
        meta=dict(row.meta),
    )


# --- Distribution and performance --------------------------------------------


def publication_to_row(publication: Publication) -> PublicationRow:
    return PublicationRow(
        id=publication.id,
        workspace_id=publication.workspace_id,
        asset_id=publication.asset_id,
        channel_key=publication.channel_key,
        external_url=publication.external_url,
        external_id=publication.external_id,
        published_at=publication.published_at,
        published_by=publication.published_by,
    )


def publication_from_row(row: PublicationRow) -> Publication:
    return Publication(
        id=row.id,
        workspace_id=row.workspace_id,
        asset_id=row.asset_id,
        channel_key=row.channel_key,
        external_url=row.external_url,
        external_id=row.external_id,
        published_at=row.published_at,
        published_by=row.published_by,
    )


def metric_to_row(metric: PerformanceMetric) -> PerformanceMetricRow:
    return PerformanceMetricRow(
        id=metric.id,
        workspace_id=metric.workspace_id,
        publication_id=metric.publication_id,
        metric=metric.metric,
        value=metric.value,
        captured_at=metric.captured_at,
        collected_by=metric.collected_by,
    )


def metric_from_row(row: PerformanceMetricRow) -> PerformanceMetric:
    return PerformanceMetric(
        id=row.id,
        workspace_id=row.workspace_id,
        publication_id=row.publication_id,
        metric=row.metric,
        value=row.value,
        captured_at=row.captured_at,
        collected_by=row.collected_by,
    )


# --- Run ledger --------------------------------------------------------------


def run_to_row(run: RunLedger) -> RunRow:
    return RunRow(
        id=run.id,
        workspace_id=run.workspace_id,
        pipeline=run.pipeline,
        status=run.status.value,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        input_tokens=run.usage.input_tokens,
        output_tokens=run.usage.output_tokens,
        cache_read_tokens=run.usage.cache_read_tokens,
        cache_write_tokens=run.usage.cache_write_tokens,
        cost_usd=run.cost_usd,
        error=run.error,
        meta=run.meta,
    )


def run_from_row(row: RunRow) -> RunLedger:
    return RunLedger(
        id=row.id,
        workspace_id=row.workspace_id,
        pipeline=row.pipeline,
        status=RunStatus(row.status),
        trigger=row.trigger,
        started_at=row.started_at,
        finished_at=row.finished_at,
        usage=TokenUsage(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_read_tokens=row.cache_read_tokens,
            cache_write_tokens=row.cache_write_tokens,
        ),
        cost_usd=row.cost_usd,
        error=row.error,
        meta=dict(row.meta),
    )


def apply_run(row: RunRow, run: RunLedger) -> None:
    row.status = run.status.value
    row.finished_at = run.finished_at
    row.input_tokens = run.usage.input_tokens
    row.output_tokens = run.usage.output_tokens
    row.cache_read_tokens = run.usage.cache_read_tokens
    row.cache_write_tokens = run.usage.cache_write_tokens
    row.cost_usd = run.cost_usd
    row.error = run.error
    row.meta = run.meta


def stage_to_row(stage: StageRecord) -> RunStageRow:
    return RunStageRow(
        id=stage.id,
        run_id=stage.run_id,
        workspace_id=stage.workspace_id,
        stage=stage.stage,
        sequence=stage.sequence,
        status=stage.status.value,
        started_at=stage.started_at,
        finished_at=stage.finished_at,
        items_in=stage.items_in,
        items_out=stage.items_out,
        input_tokens=stage.usage.input_tokens,
        output_tokens=stage.usage.output_tokens,
        cache_read_tokens=stage.usage.cache_read_tokens,
        cache_write_tokens=stage.usage.cache_write_tokens,
        cost_usd=stage.cost_usd,
        error=stage.error,
        meta=stage.meta,
    )


def stage_from_row(row: RunStageRow) -> StageRecord:
    return StageRecord(
        id=row.id,
        run_id=row.run_id,
        workspace_id=row.workspace_id,
        stage=row.stage,
        sequence=row.sequence,
        status=StageStatus(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        items_in=row.items_in,
        items_out=row.items_out,
        usage=TokenUsage(
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_read_tokens=row.cache_read_tokens,
            cache_write_tokens=row.cache_write_tokens,
        ),
        cost_usd=row.cost_usd,
        error=row.error,
        meta=dict(row.meta),
    )


def apply_stage(row: RunStageRow, stage: StageRecord) -> None:
    row.status = stage.status.value
    row.started_at = stage.started_at
    row.finished_at = stage.finished_at
    row.items_in = stage.items_in
    row.items_out = stage.items_out
    row.input_tokens = stage.usage.input_tokens
    row.output_tokens = stage.usage.output_tokens
    row.cache_read_tokens = stage.usage.cache_read_tokens
    row.cache_write_tokens = stage.usage.cache_write_tokens
    row.cost_usd = stage.cost_usd
    row.error = stage.error
    row.meta = stage.meta
