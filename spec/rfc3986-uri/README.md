# RFE-3986-URI — Minimum Viable Standard for URIs

A usage-derived subset of [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986)
(URI Generic Syntax). Every production in this grammar is here because a corpus
of **9,266,505 real-world URIs** exercised it, or because it clears a documented
security/interoperability override. Everything RFC 3986 allows but the world does
not use has been pruned. This is the shape a conformant parser has to accept — and
nothing more.

- **Grammar:** [`rfc3986-uri.mvs.abnf`](rfc3986-uri.mvs.abnf) — valid, self-closed ABNF (28 rules).
- **Evidence:** [`hits.json`](hits.json) — per-node usage counts over the corpus.
- **Decision record:** [`pruned.json`](pruned.json) — the 88 nodes removed, the threshold, and the corpus provenance.

## Provenance

| | |
| --- | --- |
| Samples parsed | **9,266,505** (of 9,282,551 collected; ~0.17% non-matching lines dropped) |
| Sources | Common Crawl `CC-MAIN-2024-10` page URLs (70%) + WAT outlinks (20%), Wikipedia `enwiki` externallinks (10%) |
| Prune threshold | **0.001** (0.1%) — a node used by fewer than 1 in 1,000 samples is a pruning candidate |
| Seed | `0` (the corpus, and therefore this grammar, is reproducible) |
| Nodes kept / pruned | 305 / 88 |

The three-source mix is deliberate: page URLs alone are ~all `http`/`https`, so
the scheme-diverse strata (link outlinks, curated external links) are what let
`mailto:`-style diversity, IPv6 hosts, and userinfo appear at all.

## The grammar

```abnf
URI          = scheme ":" hier-part [ "?" query ] [ "#" fragment ]
hier-part    = "//" authority path-abempty / path-rootless
scheme       = ALPHA *( ALPHA / DIGIT / "-" )
authority    = [ userinfo "@" ] host [ ":" port ]
userinfo     = *( unreserved / pct-encoded / sub-delims / ":" )
host         = IP-literal / IPv4address / reg-name
port         = *DIGIT
IP-literal   = "[" ( IPv6address / IPvFuture ) "]"
...
unreserved   = ALPHA / DIGIT / "-" / "." / "_" / "~"
sub-delims   = "&" / "(" / ")" / "+" / "," / ";" / "="
```

See the full file for the complete IPv6 machinery, `pchar`, and terminals.

## What was kept, and why

**Shape.** The grammar is the absolute-URI form the entire corpus used:
`scheme://[userinfo@]host[:port]/path[?query][#fragment]`. It reparses as valid
ABNF with zero dangling references.

**Two evidence-drawn boundaries tighter than RFC 3986:**

- **`scheme = ALPHA *( ALPHA / DIGIT / "-" )`.** RFC 3986 also allows `+` and `.`
  in schemes. In 9.3M URIs, `-` appeared 91 times and a digit 3 times, but `+`
  **zero** times and `.` twice. We keep `-` and digits (structurally standard —
  `s3:`, `ms-word:`) and draw the boundary short of `+`/`.`. This is the MVS doing
  its job: narrowing the spec to observed practice where the RFC is looser than it
  needs to be.
- **`sub-delims`** drops `!` `$` `'` `*` (each < 0.05%) — kept: `&` `(` `)` `+` `,`
  `;` `=`.

## What was pruned, and why

The 88 removed nodes fall into three groups, all correct for an absolute-URI spec:

1. **Relative-reference machinery** — `relative-ref`, `URI-reference`,
   `relative-part`, `path-noscheme`, `segment-nz-nc`, `path-absolute`. Every
   sampled URI was absolute (had a scheme), so the relative grammar is unused.
2. **Dead grouping rules** — `gen-delims`, `reserved`, the bare `path` wrapper.
   Their constituent characters survive via `sub-delims`/`pchar`; the grouping
   rules themselves were never the recognized node.
3. **Rare terminals** — the scheme `+`/`.` and the four rare `sub-delims` above.

## The override floor (evidence isn't everything)

Nine nodes are **protected** — kept regardless of usage — because they are
load-bearing for security or interoperability and are systematically
under-represented in a page-URL corpus. Each carries a written justification in
[`overrides.yaml`](../../pipeline/mvs_pipeline/overrides.yaml); see
[`docs/overrides.md`](../../docs/overrides.md) for the governance bar.

| Node | Owner | Why it survives despite ~zero corpus usage |
| --- | --- | --- |
| `pct-encoded` | security-wg | Decode to normalize/reject encoded traversal (`%2e%2e`) and injection |
| `userinfo` | security-wg | Recognize `user@host` to defend authority-confusion / phishing (`good.com@evil.com`) |
| `IP-literal`, `IPv6address`, `IPv4address` | security-wg | Inspect literal hosts for SSRF / private-range (`[::1]`, `169.254.169.254`) |
| `port` | protocol-wg | Non-standard-port services; same-origin identity (scheme+host+port) |
| `fragment` | protocol-wg | Client-side routing, media fragments, `#main` skip-links — never sent to servers, so crawl corpora under-count them |
| scheme `DIGIT`, `-` | protocol-wg | Standard scheme characters (`s3:`, `ms-word:`) just under threshold |

`IPvFuture` is not listed but still renders: protecting `IP-literal` pulls it back
transitively (`IP-literal = "[" ( IPv6address / IPvFuture ) "]"`).

## Validating against this spec

The native reference parser (`mvs-refparse`) accepts an input **only if** it can
be recognized without exercising any pruned node — it fails fast with a specific
`ERR_MVS_UNSUPPORTED_NODE` rather than degrading into legacy behavior:

```bash
cargo build -p mvs-refparse --release --manifest-path core/Cargo.toml
mvs-validate --ast artifacts/rfc3986-uri.ast.json \
             --pruned spec/rfc3986-uri/pruned.json \
             --uri 'https://user@host.example:8443/a/b?q=1#f'   # -> accept
mvs-validate --ast artifacts/rfc3986-uri.ast.json \
             --pruned spec/rfc3986-uri/pruned.json \
             --uri 'foo+bar://example.com/'                     # -> ERR_MVS_UNSUPPORTED_NODE
```

## Reproducing

```bash
python -m mvs_pipeline.collector.orchestrate \
  --ast artifacts/rfc3986-uri.ast.json \
  --cc-crawl CC-MAIN-2024-10  --cc-weight 0.7  --cc-limit 100 --cc-sample-rate 0.05 \
  --wat-crawl CC-MAIN-2024-10 --wat-weight 0.2 --wat-limit 8   --wat-sample-rate 0.5 \
  --wiki-dump enwiki          --wiki-weight 0.1 --wiki-sample-rate 0.05 \
  --target 10000000 --seed 0 --domain-cap 1000 --workers 8 \
  --workdir .work --out out --binary core/target/release/mvs-telemetry \
  --emit-mvs spec/rfc3986-uri/rfc3986-uri.mvs.abnf --pruned-out spec/rfc3986-uri/pruned.json
```

Same seed + same crawl → same corpus → same grammar. The provenance block on
`hits.json` / `pruned.json` ties every pruning decision back to this evidence set.
