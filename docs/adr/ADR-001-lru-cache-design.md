# ADR-001: LRU Cache Core Design & Eviction Policy

## Status
Accepted

## Context
The screening backend requires caching across three distinct stages:
1. Resume parsing results: keyed by `sha256(content)`, high computation cost (PDF text extraction and normalization).
2. GitHub repository enrichments: keyed by `github_username`, I/O-bound with strict upstream quota constraints.
3. LLM semantic judgments: keyed by `hash(resume_text + prompt_version + model)`, expensive I/O and token costs.

Previously, GitHub responses were stored in an unbounded in-memory `dict` backed by an ad-hoc JSON file. This lacked memory bounds, thread-safety, eviction, TTL expiration, and telemetry.

## Decision

### 1. Data Structure Choice: Hash Map + Doubly Linked List (DLL)
We implement a custom `LRUCache` combining:
- A hash table `dict[Key, Node]` for $O(1)$ key-to-node pointer lookup.
- A hand-crafted doubly linked list with sentinel `head` (Most Recently Used - MRU) and `tail` (Least Recently Used - LRU) nodes.
- Each `Node` contains `key`, `value`, `size_bytes`, `expires_at`, `version`, `prev`, and `next`.

On cache hit (`get`), the node is unlinked in $O(1)$ and re-inserted immediately after `head`. On insertion (`put`), a new node is placed at `head`. If bounds (`max_items` or `max_bytes`) are exceeded, nodes are unlinked from `tail.prev` in $O(1)$ until within bounds.

### 2. Hand-Crafted DLL vs. `collections.OrderedDict`
- **Primary Implementation**: Hand-crafted doubly linked list. This allows direct node-level mutation, precise tracking of `size_bytes`, and explicit in-node versioning for TTL reconciliation.
- **Reference Implementation**: We also provide `OrderedDictLRUCache` wrapping standard library `OrderedDict`. This serves as an oracle reference model for property-based tests (`hypothesis`) and microbenchmarking.

### 3. Scan Resistance: Segmented LRU (SLRU)
A batch of 50 resumes parsed in a single run represents a sequential "scan". Under basic LRU, a single batch could flush hot, frequently accessed GitHub enrichment entries.
We implement Segmented LRU (`SegmentedLRUCache`):
- **Probationary segment ($Q_{prob}$)**: New entries enter probation (e.g. 20% of capacity).
- **Protected segment ($Q_{prot}$)**: On a second access while in probation, the entry is promoted to the protected segment (80% of capacity).
- Entries evicted from $Q_{prot}$ demote back to $Q_{prob}$, protecting hot keys from scan eviction.

### 4. TTL Expiration Strategy: Lazy Expiry + Min-Heap Sweep
- **Lazy Expiry on Access ($O(1)$)**: During `get()`, if `now >= node.expires_at`, the node is lazily purged and counted as expired.
- **Active Min-Heap Sweep ($O(\log N)$ per expired entry)**: A background daemon thread periodically inspects a min-heap of `(expires_at, key, version)`. Stale heap items (due to intermediate `put()` or `pop()` operations) are detected in $O(1)$ by comparing the entry's version with the node's current version, avoiding full $O(N)$ linear scans.

## Alternatives Considered & Rejected
- **LFU (Least Frequently Used)**: Rejected due to higher bookkeeping overhead, $O(\log N)$ min-heap restructuring per access, and susceptibility to cache pollution from historical frequency bursts.
- **ARC (Adaptive Replacement Cache) / TinyLFU**: ARC is patent-encumbered/complex; TinyLFU requires Count-Min Sketch filtering. SLRU provides sufficient scan resistance with $O(1)$ overhead and simple explainability.

## Consequences
- **Positive**: Strict $O(1)$ amortized get/put/evict operations; memory footprint strictly bounded by bytes and item counts; scan-resistant.
- **Negative**: Requires careful pointer management (`prev`/`next` invariants) in the hand-crafted DLL, which will be verified using `hypothesis` property-based testing and concurrency stress tests.
