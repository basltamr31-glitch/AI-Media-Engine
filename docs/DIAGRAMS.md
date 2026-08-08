# Architecture Diagrams

The architecture as built through Phase 4, and as planned beyond it. Four
views: two C4 levels, the domain model, and the runtime sequence.

Entities shown are either implemented or scheduled in the current roadmap.
Ideas considered and deferred are recorded in `FUTURE_IDEAS.md` and deliberately
do **not** appear here.

Diagrams are Mermaid and render natively on GitHub.

---

## 1. Context (C4 Level 1)

Who uses the engine and what it talks to. Note that the engine has **no
subject-matter knowledge of its own** — that arrives as a domain pack.

```mermaid
graph TB
    operator["<b>Operator</b><br/><i>person</i><br/>Configures workspaces,<br/>runs the pipeline"]
    reviewer["<b>Reviewer / Editor</b><br/><i>person</i><br/>Edits drafts,<br/>approves content"]
    scheduler["<b>Scheduler</b><br/><i>system</i><br/>Triggers unattended runs"]

    engine["<b>AI Media Engine</b><br/><i>software system</i><br/><br/>Turns raw information into<br/>evidence-backed, published,<br/>measured content.<br/><br/>Vertical-agnostic: behaviour<br/>comes from domain packs."]

    email["<b>Email providers</b><br/>Gmail — newsletters"]
    feeds["<b>Feeds</b><br/>RSS / Atom — arXiv, blogs"]
    apis["<b>Content APIs</b><br/>Hacker News, Reddit,<br/>GitHub Releases"]
    web["<b>Primary sources</b><br/>Papers, announcements,<br/>repositories"]

    models["<b>Model providers</b><br/>Any LLM / embedding vendor.<br/>Reached only through<br/>capability ports."]

    channels["<b>Publishing platforms</b><br/>LinkedIn, X, Instagram,<br/>email, webhooks.<br/>The engine exports for them;<br/>a human posts (ADR 0018)."]
    analytics["<b>Engagement sources</b><br/>Platform metrics, entered<br/>by hand in V1"]

    packs["<b>Packs &amp; plugins</b><br/>Domain packs,<br/>source plugins"]

    operator --> engine
    reviewer --> engine
    scheduler --> engine

    engine -->|reads| email
    engine -->|reads| feeds
    engine -->|reads| apis
    engine -->|enriches from| web
    engine -->|infers via| models
    engine -->|exports for| channels
    engine -->|records readings from| analytics
    packs -.->|extends| engine

    classDef person fill:#0b5394,stroke:#073763,color:#fff
    classDef system fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class operator,reviewer,scheduler person
    class engine system
    class email,feeds,apis,web,models,channels,analytics,packs external
```

---

## 2. Containers (C4 Level 2)

Dependencies point inward only, and the rule is machine-enforced by
`lint-imports`. The dashed line is the constraint that keeps the system
provider-independent: **stages may not reach a model provider directly.**

```mermaid
graph TB
    subgraph entry["Entry points"]
        cli["<b>CLI</b><br/><i>Typer</i><br/>Composition root"]
        api["<b>HTTP API</b><br/><i>Phase 7</i><br/>Second entry point,<br/>same container"]
    end

    subgraph pipeline["engine.pipeline"]
        runner["<b>Pipeline Runner</b><br/>Executes an ordered stage list,<br/>writes the run ledger,<br/>enforces the cost ceiling"]
        stages["<b>Stages</b><br/>extract · annotate · resolve<br/>signal · rank · enrich · select"]
    end

    subgraph adapters["engine.adapters"]
        persist["<b>Persistence</b><br/>SQLAlchemy, repositories,<br/>unit of work"]
        srcs["<b>Source adapters</b><br/>Gmail · RSS · JSON API"]
        caps["<b>Capability adapters</b><br/>similarity · taxonomer<br/>entity resolver · writer"]
        provs["<b>Provider adapters</b><br/>LLM · embeddings<br/><i>the only model-aware code</i>"]
        arch["<b>Archive</b><br/>Raw payload store"]
    end

    subgraph core["engine.core — no I/O, no frameworks"]
        domain["<b>Domain</b><br/>Story · Evidence · Entity<br/>Signal · Content"]
        ports["<b>Ports</b><br/>Capability · Repository<br/>Provider · Transport"]
    end

    db[("<b>Database</b><br/>SQLite → Postgres")]
    files[("<b>Raw archive</b><br/>Filesystem → object store")]
    packdata[("<b>Packs</b><br/>Domain packs")]

    cli --> runner
    api -.-> runner
    runner --> stages
    stages --> caps
    stages --> persist
    stages --> arch
    caps --> provs
    caps --> ports
    srcs --> ports
    persist --> ports
    persist --> db
    arch --> files
    runner --> packdata
    ports --> domain

    stages -. "forbidden — enforced" .-x provs

    classDef corestyle fill:#1168bd,stroke:#0b4884,color:#fff
    classDef adapterstyle fill:#4a90d9,stroke:#2d5f8f,color:#fff
    classDef store fill:#666,stroke:#444,color:#fff
    class domain,ports corestyle
    class persist,srcs,caps,provs,arch adapterstyle
    class db,files,packdata store
```

---

## 3. Domain model

Three views of one model. Split for readability, not because the boundaries are
hard.

### 3a. Tenancy, ingestion and orchestration

```mermaid
erDiagram
    ACCOUNT ||--o{ WORKSPACE : owns
    ACCOUNT ||--o{ ACTOR : employs
    ACCOUNT ||--o{ MEMBERSHIP : grants
    ACTOR ||--o{ MEMBERSHIP : holds

    WORKSPACE ||--o{ SOURCE : configures
    SOURCE ||--o{ SOURCE_DOCUMENT : yields

    WORKSPACE ||--o{ RUN_LEDGER : executes
    RUN_LEDGER ||--o{ STAGE_RECORD : contains

    ACCOUNT {
        string id PK
        string slug UK
        string plan
        json quota
    }
    WORKSPACE {
        string id PK
        string account_id FK
        string slug UK
        string pack_key "active domain pack"
    }
    ACTOR {
        string id PK
        string account_id FK
        string key "unique per account"
        string kind "human|system|scheduler"
    }
    SOURCE {
        string id PK
        string workspace_id FK
        string kind "transport"
        string name UK "natural key"
        float trust_weight
    }
    SOURCE_DOCUMENT {
        string id PK
        string content_hash UK "idempotency"
        text raw_content "archived verbatim"
    }
```

### 3b. Intelligence

Phase 2 builds this view. `STORY` is the deduplicated unit; `EVIDENCE` is the
provenance chain that lets any published claim be traced to a primary source.

```mermaid
erDiagram
    SOURCE_DOCUMENT ||--o{ STORY_CANDIDATE : "extracts to"
    STORY ||--o{ STORY_CANDIDATE : "clusters"
    STORY ||--o{ STORY_LINK : "relates to"
    STORY ||--o{ EDITORIAL_SCORE : "scored by"
    STORY ||--o{ SIGNAL_VALUE : "measured by"

    STORY }o--o{ ENTITY : mentions
    ENTITY ||--o{ ENTITY_ALIAS : "matched by"
    STORY ||--o{ EVIDENCE : "supported by"

    STORY {
        string id PK
        string status
        string canonical_headline
        string primary_url
    }
    EVIDENCE {
        string id PK
        string story_id FK
        string claim "in our own words"
        string source_url
        string kind "primary|secondary|aggregator"
        string quote "<=25 words, validated"
    }
    ENTITY {
        string id PK
        string canonical_name UK
        string entity_type_key "pack-defined"
        int tier
    }
```

### 3c. Content and learning

The revision chain preserves what the model wrote alongside what the human
published. The signal log records outcomes. Both are append-only.

```mermaid
erDiagram
    STORY ||--o{ CONTENT_ASSET : "produces"
    CONTENT_ASSET ||--o{ CONTENT_REVISION : "versioned by"
    CONTENT_REVISION ||--o| CONTENT_REVISION : "derived from"
    CONTENT_ASSET ||--o{ PUBLICATION : "published as"
    PUBLICATION ||--o{ PERFORMANCE_METRIC : "measured by"

    SIGNAL }o--|| ACTOR : "attributed to"
    CONTENT_REVISION ||--o{ SIGNAL : emits
    PERFORMANCE_METRIC ||--o{ SIGNAL : emits

    CONTENT_REVISION {
        string id PK
        int revision_number
        text body
        string origin "generated|edited|restored"
        string actor_id FK
        string prompt_version
    }
    SIGNAL {
        string id PK
        string signal_type "open vocabulary"
        string polarity
        float value
        datetime occurred_at "when it happened"
        datetime recorded_at "when we learned"
    }
```

---

## 4. Pipeline sequence

One daily run, as built. Each stage is a separate `engine <stage> run`; the
runner records a `StageRecord` for every one and checks the cost ceiling
between them.

Two things are worth reading carefully, because both differ from the obvious
design:

- **Enrichment runs before ranking, not after.** Selection refuses any story
  without a primary source read first-hand, so a story that was never enriched
  can never be chosen — ranking it first would be ranking candidates that are
  ineligible by definition. Fetch cost is bounded instead by a per-run ceiling
  (25 stories) and an attempt count that retires dead links, not by score.
- **The cost ceiling is checked twice.** Between stages by the runner, and
  again *before each model call* inside generation — three stories across three
  channels and two languages is eighteen calls inside one stage, and a
  between-stages check would notice the overspend only after it happened.

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant Runner as Pipeline Runner
    participant Stage
    participant Ports as Ports (fetcher / parser / extractor / LLM)
    participant Repo as Repositories
    participant FS as output/

    CLI->>Runner: run(workspace, stages)
    Runner->>Repo: create RunLedger (status=running)

    rect rgb(240, 246, 252)
    note over Runner,Repo: INGEST — sources to SourceDocument
    Runner->>Stage: ingest
    Stage->>Ports: SourceFetcher.fetch(source)
    Stage->>Repo: store documents (deduped by content hash)
    end

    rect rgb(240, 246, 252)
    note over Runner,Repo: PARSE — documents to StoryCandidate
    Runner->>Stage: parse
    Stage->>Ports: DocumentParser.parse(document)
    Stage->>Repo: store candidates
    end

    rect rgb(240, 246, 252)
    note over Runner,Repo: RESOLVE — cluster mentions into canonical stories
    Runner->>Stage: resolve
    Stage->>Repo: url_key / title_key matching, then fuzzy
    Stage->>Repo: stories (status=resolved)
    end

    rect rgb(255, 248, 240)
    note over Runner,Ports: ENRICH — the only stage that touches the open web<br/>bounded per run, one request at a time
    Runner->>Stage: enrich
    Stage->>Ports: HttpClient.get(primary_url)
    Stage->>Ports: ContentExtractor.extract(html)
    Stage->>Repo: EnrichedSource (status=enriched)
    end

    rect rgb(240, 246, 252)
    note over Runner,Repo: EVIDENCE then SCORE — provenance, then explainable ranking
    Runner->>Stage: evidence
    Stage->>Repo: Evidence rows (primary / secondary / aggregator)
    Runner->>Stage: score
    Stage->>Repo: EditorialScore with per-signal components
    end

    rect rgb(240, 246, 252)
    note over Runner,Repo: SELECT — the stage that says no
    Runner->>Stage: select
    Stage->>Repo: minimum score, primary source, daily quota
    Stage->>Repo: status=selected, or a counted refusal reason
    end

    rect rgb(255, 248, 240)
    note over Runner,Ports: GENERATE — brand voice x channel x language
    Runner->>Stage: generate
    loop per story, channel and language
        Stage->>Stage: check spend against the ceiling
        Stage->>Ports: LLMPort.generate_structured(request, schema)
        Stage->>Repo: ContentAsset + append ContentRevision
    end
    end

    rect rgb(240, 246, 252)
    note over Runner,FS: RENDER — the hand-off out of the engine
    Runner->>Stage: render
    Stage->>Repo: selected stories, scores, evidence, current revisions
    Stage->>FS: package.json (canonical) + post.md (the view of it)
    end

    Runner->>Repo: finalise RunLedger (status, usage, cost, duration)
    Runner-->>CLI: RunLedger

    note over CLI,FS: Assets are created in `draft`. Human review is the gate,<br/>and the generated-to-published diff is kept as revisions.<br/>Review, export, publication and measurement are driven<br/>one artifact at a time, outside the pipeline (ADR 0018).
```

---

## Reading the diagrams together

| Question | Diagram |
|---|---|
| Who uses this and what does it touch? | 1 |
| How is the code layered, and what may not import what? | 2 |
| What is remembered, and what is recomputable? | 3a–3c |
| What happens on a run, and where is money spent? | 4 |

The load-bearing constraints, visible across all four:

1. **The domain core has no I/O** (2) — enforced by `lint-imports`.
2. **Stages never touch a model provider** (2) — only `LLMPort`, so which model
   writes is configuration.
3. **Nothing is generated from a source nobody read first-hand** (4) —
   enrichment precedes selection, and selection refuses stories without a
   primary source. This is the constraint that keeps the engine from becoming
   a republisher.
4. **Content revisions and signals are append-only** (3c) — what the model wrote
   is never overwritten by what the human published. Publications and
   performance metrics are append-only for the same reason: an outcome that
   can be quietly revised is not evidence of anything.
