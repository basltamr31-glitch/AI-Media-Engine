# ADR 0003: Multi-tenancy from the first commit

- **Status:** Accepted
- **Date:** Phase 0

## Context

The product will run single-tenant for roughly a year: one workspace, one
operator, publishing to one set of channels. Multi-tenancy is not needed until
the done-for-you service phase (months 9–15 of the roadmap).

The temptation is to defer it. The counter-argument is that retrofitting tenancy
into a live system with real customer data is one of the most expensive
migrations in software: every table needs a column and a backfill, every query
needs an audit, and every missed `WHERE` clause is a data-leak incident between
paying customers.

## Decision

Model tenancy from the first commit, even while exactly one tenant exists.

1. `Workspace` is the tenant root entity.
2. Every tenant-owned domain entity inherits `TenantScoped`, which carries
   `workspace_id`.
3. Every table has a `workspace_id` column with a foreign key and an index.
4. Every repository method takes `workspace_id` as a parameter and filters on it.
   There is no query path that omits the scope.
5. Configuration is per-workspace (`config/workspaces/<slug>.yaml`), so a second
   tenant is a second file.

Explicitly **not** built now: authentication, authorisation, per-tenant billing,
usage quotas, row-level security policies, or a tenant-management UI.

## Alternatives considered

**Add tenancy when the first customer arrives.** Rejected. The cost is not the
column; it is auditing every query written in the interim and backfilling live
data. The work also lands exactly when attention is on the customer.

**Database-per-tenant.** Stronger isolation, but heavy operationally, and it
makes cross-tenant analytics — which is where the feedback-loop moat lives —
awkward. Revisit only if a customer has a regulatory requirement.

## Consequences

**Positive**

- Tenant isolation is enforced by method signatures, so the type checker catches
  omissions rather than a customer discovering them.
- Onboarding tenant #2 is a config file and a `seed` command.
- No data migration between single- and multi-tenant operation.

**Negative**

- Slight verbosity: `workspace_id` threads through nearly every signature.
- The single-tenant deployment carries a tiny amount of unused machinery.

Both are trivially outweighed. This is the cheapest insurance in the codebase.
