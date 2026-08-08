"""The human-readable view of a day's output.

``post.md`` is for a person deciding what to publish: every post, grouped by
story, with the score that selected it and the source behind it. It is a
*view* of ``package.json`` rather than a second source of truth, so the two
cannot disagree.

## Why the Arabic is wrapped in HTML

Markdown has no way to state text direction, and every renderer guesses from
the first strong character in a paragraph. That guess is wrong exactly when it
matters most: an Arabic paragraph that opens with a Latin product name --
"MatrAIx يقدم..." -- is laid out left-to-right, which puts the full stop on the
wrong side and reorders any parenthetical. The result is not "slightly off"; it
is unreadable to an Arabic reader, and it looks fine to everyone else, so
nobody catches it.

Wrapping each Arabic paragraph in ``<p dir="rtl">`` states the base direction
instead of leaving it to inference. HTML is legal in Markdown and ``dir`` is
honoured by GitHub, VS Code, Obsidian and every browser, so this is the
portable answer rather than a GitHub trick.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any, Final

#: Scripts written right-to-left. Checked against the *language tag* rather
#: than sniffed from the text, because a mostly-Latin Arabic post (a release
#: note thick with product names) is still an Arabic post.
_RTL_LANGUAGES: Final = frozenset({"ar", "fa", "he", "ur", "ps", "sd", "yi"})

_PARAGRAPH_BREAK: Final = re.compile(r"\n\s*\n")


def is_rtl(language: str) -> bool:
    return language.split("-")[0].lower() in _RTL_LANGUAGES


def render_body(body: str, *, language: str) -> str:
    """One post's text, laid out in the right direction.

    Left-to-right text is emitted as-is: it is already Markdown, and wrapping
    it in HTML would only stop emphasis and links from rendering.
    """
    text = body.strip()
    if not text:
        return "_(empty)_"
    if not is_rtl(language):
        return text

    paragraphs = [part.strip() for part in _PARAGRAPH_BREAK.split(text) if part.strip()]
    return "\n\n".join(
        # Escaped because this is now an HTML context: an unescaped `<` in a
        # post would silently swallow the rest of the paragraph.
        f'<p dir="rtl" align="right">{escape(paragraph).replace(chr(10), "<br>")}</p>'
        for paragraph in paragraphs
    )


def _story_section(index: int, story: dict[str, Any]) -> list[str]:
    lines = [f"## {index}. {story['headline']}", ""]

    score = story.get("score")
    if score:
        components = ", ".join(
            f"{name} {value:.2f}" for name, value in sorted(score["components"].items())
        )
        lines.append(f"**Score** {score['total']:.4f} — {components}")
    if story.get("primary_url"):
        lines.append(f"**Source** <{story['primary_url']}>")

    evidence = story.get("evidence") or []
    if evidence:
        primary = sum(1 for record in evidence if record["kind"] == "primary")
        lines.append(f"**Evidence** {len(evidence)} record(s), {primary} primary")
    lines.append("")

    assets = story.get("assets") or []
    if not assets:
        # A selected story with nothing written for it is a real state --
        # generation refused it, or has not run. Saying so beats an empty
        # section that reads like the renderer broke.
        lines += ["_Selected, but nothing written yet. Run: engine generate run_", ""]
    for asset in assets:
        lines += _asset_section(asset)
    return lines


def _asset_section(asset: dict[str, Any]) -> list[str]:
    channel = asset.get("channel") or asset["product"]
    heading = f"### {channel} · {asset['language']}"
    lines = [heading, ""]
    lines.append(render_body(asset["body"], language=asset["language"]))
    lines.append("")

    facts = [f"{asset['characters']} characters", asset["state"]]
    if asset["revision"]["number"]:
        facts.append(f"revision {asset['revision']['number']}")
    if asset["revision"]["prompt_version"]:
        facts.append(asset["revision"]["prompt_version"])
    footer = " · ".join(facts)
    hashtags = asset.get("hashtags") or []
    if hashtags:
        footer += " · " + " ".join(f"#{tag}" for tag in hashtags)
    lines += [f"<sub>{escape(footer)}</sub>", ""]
    return lines


def render_markdown(package: dict[str, Any]) -> str:
    """Render a package as the day's readable edition."""
    counts = package["counts"]
    lines = [
        f"# {package['workspace']} — {package['edition']}",
        "",
        f"{counts['stories']} story(ies), {counts['assets']} post(s). "
        f"Generated {package['generated_at']} by engine {package['engine_version']}.",
        "",
    ]
    if package.get("cost_usd"):
        lines += [f"Generation cost: ${package['cost_usd']:.4f}.", ""]

    if not package["stories"]:
        # A thin day is a legitimate outcome, and a file that says so beats an
        # empty one that reads like a failed run.
        lines += ["_Nothing was selected for this edition._", ""]
        return "\n".join(lines)

    lines += ["---", ""]
    for index, story in enumerate(package["stories"], start=1):
        lines += _story_section(index, story)
        lines += ["---", ""]

    # A trailing newline: files without one are a diff hunk on every edit.
    return "\n".join(lines).rstrip() + "\n"
