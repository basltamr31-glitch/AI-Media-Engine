"""Turning an approved asset into the exact text to paste into a channel.

``render`` produces an edition: everything about a day's output, for a human
to read and a machine to consume. This produces something narrower and more
literal -- one file per approved post, containing what goes in the box and
nothing else. No headings, no metadata, no provenance. Selecting all and
copying must yield a publishable post.

The channel's rules come from the pack (ADR 0015), so adding Threads is a pack
edit here exactly as it is everywhere else: a channel that forbids links drops
the link, one that allows two hashtags gets two, and the character limit is
checked against the assembled text rather than against the body the model
wrote -- the hashtags and the URL count too, and a post that fits before they
are appended is not a post that fits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.core.domain.content import ContentAsset, ContentRevision, normalise_hashtags
from engine.core.domain.pack import ChannelDef
from engine.core.domain.story import Story


@dataclass(frozen=True)
class ChannelExport:
    """One post, formatted for one channel, with anything wrong about it named."""

    asset_id: str
    channel_key: str
    language: str
    filename: str
    text: str
    headline: str
    #: Problems a human must resolve before publishing. Reported rather than
    #: fixed: silently truncating a post to fit is how a sentence ends mid-word
    #: in front of an audience.
    warnings: list[str] = field(default_factory=list)

    @property
    def characters(self) -> int:
        return len(self.text)

    @property
    def is_publishable(self) -> bool:
        return not self.warnings


def format_for_channel(
    asset: ContentAsset,
    revision: ContentRevision | None,
    channel: ChannelDef | None,
    story: Story | None,
) -> ChannelExport:
    """Assemble the publishable text for one asset.

    A missing channel definition is not an error here. The pack may have been
    changed since the asset was generated, and refusing to export the text a
    human already approved would be the wrong response -- the body is exported
    unformatted and the mismatch is reported as a warning.
    """
    body = (revision.body if revision else "").strip()
    warnings: list[str] = []
    if not body:
        warnings.append("no text: the asset has no revision to export")

    hashtags = normalise_hashtags(asset.meta.get("hashtags"))
    link = story.primary_url if story else None

    if channel is None:
        warnings.append(
            f"channel {asset.channel_key!r} is not in the active pack; "
            "exported without channel formatting"
        )
        parts = [body]
    else:
        parts = [body]
        if channel.supports_hashtags and hashtags:
            allowed = hashtags[: channel.max_hashtags]
            if len(hashtags) > channel.max_hashtags:
                warnings.append(
                    f"{len(hashtags)} hashtags generated, {channel.max_hashtags} allowed; "
                    "kept the first"
                )
            if allowed:
                parts.append(" ".join(f"#{tag}" for tag in allowed))
        elif hashtags:
            warnings.append(f"{channel.name} does not use hashtags; dropped {len(hashtags)}")

        if link:
            if channel.supports_links:
                parts.append(link)
            else:
                # Instagram and friends. Not a defect -- the operator needs to
                # know the source went missing so they can put it elsewhere.
                warnings.append(f"{channel.name} does not support links; source URL omitted")

    text = "\n\n".join(part for part in parts if part)

    if channel is not None and len(text) > channel.max_characters:
        warnings.append(
            f"{len(text)} characters, {channel.max_characters} allowed on {channel.name}; "
            "shorten it before publishing"
        )

    return ChannelExport(
        asset_id=asset.id,
        channel_key=asset.channel_key or "",
        language=str(asset.language),
        filename=export_filename(asset),
        text=text,
        headline=story.canonical_headline if story else "",
        warnings=warnings,
    )


def export_filename(asset: ContentAsset) -> str:
    """A stable, sortable name for one asset's export file.

    The asset id is included because channel and language alone are not unique
    -- two stories produce two LinkedIn posts in Arabic on the same day -- and
    a name that collides would silently export one of them.
    """
    channel = asset.channel_key or "nochannel"
    return f"{channel}-{asset.language}-{asset.id}.txt"
