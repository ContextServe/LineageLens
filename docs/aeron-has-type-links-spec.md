# LineageLens HAS_TYPE Peer Links Spec

> **Status:** Decisions recorded. Query behavior follows [§7](#7-decisions).  
> **Behavior:** `impact_of`, `explain`, and `find_paths` report a shared hub when the edges support it. The indexer and the contract adapters are unchanged. Search stays a name lookup.

## Objective

On a Rust repository, LineageLens stored only `HAS_TYPE` from each Aeron send site and each Aeron receive site onto the same command enum. No messaging adapter ran. `search`, `explain`, `impact_of`, and `find_paths` did not surface the matching peer.

Go channels fail the same way. Kafka and HTTP fail the same way when no adapter matches. When a contract node does exist, `search`, `explain`, and `find_paths` still miss the peer, and `impact_of` is one-sided.

The two gaps below are implemented as [§7](#7-decisions) decides them. An entry states only what the edges record. `verification` is present only when those edges do not give a direction.

---

## 1. What the Rust run stored

The index did not use a messaging adapter, an Aeron detector, or an `EXPOSES` / `CONSUMES` contract. Those adapters exist for Kafka, Celery, and SQS. They did not fire on this repository.

The only relationship stored for the send site and the receive site was `HAS_TYPE` onto the same command enum:

```text
sender fn  --HAS_TYPE-->  Command enum  <--HAS_TYPE--  receiver fn
```

`RefKind.TYPE` becomes that edge in `src/lineagelens/resolve/resolver.py`. Rust emits the reference from any `type_identifier` in `src/lineagelens/spec/rust/refs.scm`. The shared enum name is why both sites land on one type node.

Search and blast radius still did not surface the receiver from the sender.

---

## 2. Why the tools miss the peer

Putting `HAS_TYPE` on an allowlist is not sufficient. For blast radius it is already there.

| Tool | What it does today | Why the receiver stays hidden |
|---|---|---|
| `impact_of` | Walks `HAS_TYPE` through `DEPENDENT_EDGES` in `src/lineagelens/query/analysis.py` | The walk is backward: who depends on the symbol you named. From the sender, the enum is an outgoing type, so the receiver is never reached. Blast radius of the enum lists both sites as dependents of the type, which is a different question. |
| `search` | BM25 over nodes only, in `src/lineagelens/query/api.py` | It never reads edges. |
| `explain` | Returns only a direct `src → dst` edge, in `src/lineagelens/query/analysis.py` | There is no sender-to-receiver edge. |
| `find_paths` | Forward-only, in `src/lineagelens/query/traverse.py`, defaulting to call-chain kinds (`CALL_CHAIN_EDGES` in `src/lineagelens/core/kinds.py`) | Sender → enum stops. The receiver’s `HAS_TYPE` points at the enum, so a forward walk never leaves the type toward the other site. |

The missing behavior is a sibling hop over the graph that was actually built: from the sender, follow `HAS_TYPE` to the enum, then to every other site that `HAS_TYPE` that same enum.

```text
sender --HAS_TYPE--> Command <--HAS_TYPE-- receiver
                         ^
                         |
                    other co-user
```

---

## 3. Go channels

Go channels are the same miss. Nothing in `src/lineagelens/spec/go/refs.scm` captures `ch <- v` or `<-ch`, and `src/lineagelens/contracts/definitions/messaging.yaml` has no Go adapter. Two functions that share a `chan T` are not paired. At best each mentions the element type `T` via `HAS_TYPE`.

There is no channel-identity edge and no contract hub to walk.

---

## 4. Kafka and HTTP

Kafka and HTTP split in two, depending on whether an adapter already joined the two sides.

### 4.1 No adapter matches

Unmatched clients are the same miss as the Rust run.

- There is no Go or Rust Kafka adapter (sarama, kafka-go, or a wrapped producer).
- HTTP route adapters cover FastAPI, Flask, Django, Spring, Express, Nest, ASP.NET, and Go mux only (`src/lineagelens/contracts/definitions/http.yaml`). gin, chi, and `http.Client` do not join.

Two sides that only share a request struct or a message struct are `HAS_TYPE` again. A sibling hop can see that shared type. It does not know that a topic or a route is the real key, because no contract node was stored.

### 4.2 An adapter matches

When an adapter does match — Java `@KafkaListener` / `send`, a Python producer or consumer, or a normalised HTTP route — the graph gets a contract node. Both `EXPOSES` and `CONSUMES` point into that hub (`CONTRACT_EDGES` in `src/lineagelens/core/kinds.py`). Those edges are outside `DEPENDENT_EDGES`.

| Tool | What a reviewer sees |
|---|---|
| `search` | Still misses the peer. Search does not read the hub. |
| `explain` | Still misses the peer. There is no direct symbol-to-symbol edge. |
| `find_paths` | Still misses the peer. The walk is forward-only, and the contract node has no outgoing edge to the other side. |
| `impact_of` | One-sided. `_contract_consumers` in `src/lineagelens/query/analysis.py` adds consumers only when the named symbol `EXPOSES` the contract, and it skips a consumer whose `service_id` equals the target’s `service_id`. |
| `contract_map` | Lists both sides. This is the tool that already shows the join. |

`js.kafkajs` in `src/lineagelens/contracts/definitions/messaging.yaml` marks both `producer.send` and `consumer.subscribe` as `exposes`. The subscriber never joins the topic as a consumer, so that consumer stays off the hub.

A `HAS_TYPE` sibling hop covers the Aeron enum, a Go channel element type, and a shared DTO. It does not know that a Kafka topic or an HTTP route is the real key.

---

## 5. Proposed gaps

One hop does not cover a shared type and a matched contract. Both are the same graph shape: two sites point at one hub. The implementation is that shape, not a detector for one framework. The choices in [§7](#7-decisions) are what keep a hub from being reported as a flow the index did not store.

### Gap 1 — `HAS_TYPE` sibling hop

For a shared command enum, a Go `chan T` element type, and a shared request or message struct.

From a site, follow `HAS_TYPE` to the type node, then to every other site that `HAS_TYPE` that same type. On `impact_of`, this is a new relation after the normal dependent walk. It is not another kind added to `DEPENDENT_EDGES`.

What this uses:

- The edges the Rust index already stored. No reindex, and no Aeron adapter.
- Same-language pairs that share one command type, so they can show up in blast radius and explain once a tool grows the hop.
- The tools that failed on this repository.

What this does not mean:

- The hop is “shares a type.” It is not “sends to that receiver.” Every other user of that enum is a hit.
- Unfiltered, the same hop also ties together co-users of `String`, `Option`, `Vec`, and `Result`. The filter that keeps that noise out is [decision 1](#7-decisions): a declaration file plus a fan-in cap, not a denylist of those names.
- It fails when the other side does not reuse the type name. That case was not in this Rust run. It needs a graph fact other than `HAS_TYPE`, which this index did not produce.
- It does not cross languages. Resolution is language-scoped, so a Rust enum and a Java class never become one node.
- It misses sites that never put the type in type position: a Rust variant path, a Java `UnsafeBuffer` offer, or encoder and decoder class names that differ.

Which tools grow this hop is [decision 2](#7-decisions).

### Gap 2 — contract-hub walk

For Kafka and HTTP once an adapter has already joined the two sides.

`search`, `explain`, and `find_paths` never cross the hub. `impact_of` lists consumers only from the exposer and drops same-service peers. `contract_map` already lists both sides. Go channels and unmatched Kafka or HTTP clients have no hub, so this walk does not apply to them.

The walk stays on the contract kinds the adapter already stored. Same-service peers are included, and they are not labeled cross-service. Both are [§7](#7-decisions).

### What this run did not use

An Aeron ground contract — channel plus stream id, stored as `EXPOSES` / `CONSUMES` — is not the graph this run built. Building one would be new detection and a reindex. Once those edges exist, the contract-hub walk in `peers` reports them, including a same-service peer. `_contract_consumers` still fills `cross_service` only for a consumer in another service. That detector is a later spec if the product wants pub/sub semantics. It does not explain this Rust result.

---

## 6. Language matrix

The sibling hop succeeds only when both sides resolve to the same type node.

| Case | Shared type node |
|---|---|
| Rust, Java, Go, C# | Yes, when the shared name resolves to a type. |
| TypeScript | Yes. `@ref.type` is broad. |
| Python | Yes, from annotations only. |
| JavaScript | No. There is no `@ref.type`. |
| Kotlin, Ruby, Bash, C | No. The hop does not fire. |
| Cross-language | No. Resolution is language-scoped. A Rust enum and a Java class stay two nodes. |
| Type not in type position | No edge to the shared node. Examples: a Rust variant path, a Java `UnsafeBuffer` offer, encoder vs decoder class names. |

---

## 7. Decisions

Each choice is the one that lets an answer state a stored fact, and withholds a claim the edges do not support. `verification` is how an ambiguous flow is handed to an agent: the field is present only then, and it says to read the code. It is absent when the direction was recorded.

1. **Sibling-hop filter.** Any declared type, not enums only and not a denylist of `String`, `Option`, `Vec`, or `Result`. The hub must be a type node with a declaration file. A type referenced from more than 16 distinct sites is not a peer list; the envelope records `peer_fan_in` and names no co-user. A type with no declaration file and more than one site records `peer_undeclared_type` and names no peer. The ordinary dependent walk of the type itself is unchanged: that question is who references the type, and the edges answer it.

2. **Which tools grow the hop.** `impact_of` adds a `peers` list after the dependent walk. `explain` reports the inward hub path when no direct edge exists. `find_paths` reports a `shared_hub` result only when the forward walk found nothing, so a type share cannot outrank a call chain. `search` stays name lookup. A peer attached to a name hit would read as a match.

3. **Contract kind.** The walk uses the kind already stored on the contract edge (`topic`, `http_route`, and the other `ContractKind` values). No new kind is added. A kind the adapter did not write is not invented in the answer.

4. **Same-service contract peers.** They are included in `peers` with `service_relation` `same_service`. They are not placed in `cross_service`. `cross_service` remains what it was: other services that consume a contract the target exposes. A missing `service_id` is `unknown`, not same-service and not cross-service.

A contract peer whose roles are exactly one `EXPOSES` and one `CONSUMES` states those roles and the weaker evidence tier, and it has no `verification` field. A shared type, two exposes, two consumes, or a site that both exposes and consumes carries `verification`.

---

## 8. Out of scope

- Indexer, resolver, grammar, or contract-adapter changes.
- An Aeron channel / stream-id detector.
- Turning `search` into a relationship query.
