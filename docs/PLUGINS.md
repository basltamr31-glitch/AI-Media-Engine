# Writing a plugin

The engine discovers implementations through Python **entry points** (ADR 0017).
Installing a package is enough — there is no registration file to edit, no
import to add to the composition root, and no fork of this repository.

The engine's own fetchers are declared exactly this way, so the extension path
is exercised on every run rather than only by third parties.

## The contract

An entry point resolves to a **factory**: a callable taking one argument, a
`PluginContext`, and returning the implementation.

```python
# engine_youtube/fetcher.py
from engine.adapters.plugins import PluginContext
from engine.core.domain.enums import SourceKind
from engine.core.domain.source import Source, SourceDocument


class YouTubeFetcher:
    @property
    def kind(self) -> SourceKind:
        return SourceKind.API

    def fetch(self, source: Source, *, limit: int = 50) -> list[SourceDocument]: ...


def build_fetcher(context: PluginContext) -> YouTubeFetcher:
    return YouTubeFetcher(http=context.http, clock=context.clock)
```

```toml
# engine-youtube/pyproject.toml
[project.entry-points."engine.fetchers"]
youtube = "engine_youtube.fetcher:build_fetcher"
```

```bash
pip install engine-youtube
```

`PluginContext` carries `settings`, `clock`, `http` and `resolve_path`. It is
deliberately small: everything on it is a port or configuration, so a plugin
never imports the engine's infrastructure.

## Groups

| Group | Protocol | Registry key |
|---|---|---|
| `engine.fetchers` | `SourceFetcher` | the fetcher's `kind` |
| `engine.parsers` | `DocumentParser` | the parser's `parser_key` |

Groups for signals, renderers and destinations are added when the registry that
consumes them exists. An advertised group nothing reads is a promise the engine
does not keep.

Note the registry key comes from the **object**, not from the entry-point name.
Name them the same anyway; `engine plugins list` shows both, and a mismatch is
confusing to whoever debugs it next.

## What happens when a plugin misbehaves

Every failure costs exactly that plugin. The engine starts, every other plugin
loads, and the reason is recorded:

- the module does not import
- the factory raises
- the factory returns something that does not implement the expected protocol
- the registry key is already taken

```bash
engine plugins list
```

```
 Group     Name    Provider              Entry point                     Status  Detail
 fetchers  rss     ai-media-engine 0.1.0 …sources.rss:build_fetcher      loaded
 fetchers  youtube engine-youtube 1.2.0  …fetcher:build_fetcher          failed  ImportError: No module named 'yt_dlp'
```

`engine doctor` also reports the count and fails if anything did not load.

## Known limits

**New transports need a `SourceKind`.** `FetcherRegistry` keys on
`SourceKind`, which is a closed enum (`email`, `rss`, `api`, `web`) because the
engine controls transports. A third-party fetcher for a genuinely new transport
can reuse the closest kind — most HTTP-shaped sources are `api` — but cannot
introduce its own value without a change here. Parsers have no such limit:
`parser_key` is a free string. See `docs/FUTURE_IDEAS.md`.

**Plugins run with full engine privileges.** This is true of every Python plugin
system. Install deliberately. Sandboxing is tied to hosted multi-tenancy and is
not a concern this phase addresses.

## Developing in this repository

Entry points are baked into installed metadata, not read from the source tree.
After editing the `[project.entry-points…]` blocks in `pyproject.toml`:

```bash
pip install -e . --no-deps
```

Until you do, the change is invisible — including to the test suite.
