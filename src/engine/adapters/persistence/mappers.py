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
    MembershipRow,
    RunRow,
    RunStageRow,
    SignalRow,
    SourceDocumentRow,
    SourceRow,
    WorkspaceRow,
)
from engine.core.domain.account import Account, Actor, Membership, Quota
from engine.core.domain.enums import (
    ActorKind,
    Language,
    MembershipRole,
    PlanTier,
    RunStatus,
    SignalPolarity,
    SourceKind,
    StageStatus,
    SubjectType,
)
from engine.core.domain.ledger import RunLedger, StageRecord, TokenUsage
from engine.core.domain.signal import Signal
from engine.core.domain.source import Source, SourceDocument
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
        languages=[language.value for language in workspace.languages],
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
        languages=[Language(value) for value in row.languages],
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
    row.languages = [language.value for language in workspace.languages]
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
