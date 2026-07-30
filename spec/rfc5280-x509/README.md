# RFE-5280-X509 — Minimum Viable Standard for X.509 Certificates

A usage-derived subset of [RFC 5280](https://www.rfc-editor.org/rfc/rfc5280)
(the Internet X.509 certificate profile). Every ASN.1 production in this grammar
is here because a corpus of **21,536 real-world certificates** exercised it, or
because it clears a documented security/interoperability override. Everything
RFC 5280 permits but current Web PKI does not use has been pruned.

This is the same method as [`RFE-3986-URI`](../rfc3986-uri/), applied to a
**binary ASN.1** grammar rather than a text one — the proof that it generalizes.

- **Specification:** [`RFE-5280.md`](RFE-5280.md) — the normative document: syntax, the **deltas from RFC 5280**, override floor, security & conformance.
- **Grammar:** [`rfc5280-x509.mvs.asn1`](rfc5280-x509.mvs.asn1) — the minified ASN.1 module.
- **Evidence:** [`hits.json`](hits.json) — per-node usage counts over the corpus.
- **Decision record:** [`pruned.json`](pruned.json) — the 16 nodes removed, the threshold, and provenance.

## Provenance

| | |
| --- | --- |
| Certificates parsed | **21,536** (all matched, 0 skipped) |
| Source | Certificate Transparency log `ct.googleapis.com/logs/us1/argon2026h1`, from index 0, seed 0 |
| Prune threshold | **0.001** (0.1%) |
| Nodes kept / pruned | 97 / 16 |

Certificates were fetched via RFC 6962 `get-entries` (`mvs_pipeline.ct`), decoded
to DER, and walked by `mvs-telemetry --der-dir`. A CT log is append-only, so the
fixed `(log, start, seed)` reproduces the same corpus. The certificate structure
is highly uniform — nodes are either near-100% present or entirely absent — so
this corpus cleanly separates in-use from unused productions; a larger scan would
mainly probe the long tail already covered by the override floor.

## The grammar

```asn1
Certificate ::= SEQUENCE { tbsCertificate TBSCertificate, signatureAlgorithm AlgorithmIdentifier, signatureValue BIT STRING }
TBSCertificate ::= SEQUENCE { version [0] EXPLICIT Version OPTIONAL, serialNumber CertificateSerialNumber, signature AlgorithmIdentifier, issuer Name, validity Validity, subject Name, subjectPublicKeyInfo SubjectPublicKeyInfo, extensions [3] EXPLICIT Extensions OPTIONAL }
DirectoryString ::= CHOICE { printableString PrintableString, utf8String UTF8String }
Time ::= CHOICE { utcTime UTCTime, generalTime GeneralizedTime }
Extension ::= SEQUENCE { extnID OBJECT IDENTIFIER, critical BOOLEAN OPTIONAL, extnValue OCTET STRING }
```

See [`rfc5280-x509.mvs.asn1`](rfc5280-x509.mvs.asn1) for the full module.

## Two evidence-drawn boundaries tighter than RFC 5280

- **`DirectoryString`** keeps only `printableString` (100% of certs) and
  `utf8String` (68%); the deprecated `teletexString`, `bmpString`, and
  `universalString` encodings had **zero** occurrences and are excluded. A
  conforming parser rejects a DN using them — a deliberate reduction of a
  historically attack-prone surface.
- **v2 unique identifiers** (`issuerUniqueID` `[1]`, `subjectUniqueID` `[2]`) are
  removed — RFC 5280 §4.1.2.8 says CAs SHOULD NOT generate them, and none did.

## The override floor

Kept regardless of usage (see [`overrides.yaml`](../../pipeline/mvs_pipeline/overrides.yaml)):

| Production | Why it survives |
| --- | --- |
| `Extension` `critical` | Gates unknown-critical-extension rejection (RFC 5280 §4.2). |
| `Time` `generalTime` / GeneralizedTime | Mandatory for validity dates ≥ 2050 (long-lived CAs) — **zero** occurrences in today's leaf certs, kept anyway. |
| `DirectoryString` `printableString` | Ubiquitous DN encoding; protected against a sample skewed to UTF8String. |

## Validating against this spec

```bash
cargo build -p mvs-refparse --release --manifest-path core/Cargo.toml
mvs-validate --ast artifacts/rfc5280-x509.ast.json \
             --pruned spec/rfc5280-x509/pruned.json \
             --der <some-cert.der>          # accept / ERR_MVS_UNSUPPORTED_NODE
```

## Reproducing

```bash
python -m mvs_pipeline.ct --log https://ct.googleapis.com/logs/us1/argon2026h1/ \
  --start 0 --count 1000000 --sample-rate 1.0 --seed 0 --out-dir corpus/certs-real
mvs-telemetry --ast artifacts/rfc5280-x509.ast.json \
  --der-dir corpus/certs-real --out spec/rfc5280-x509/hits.json
# then prune + codegen under overrides.yaml (see docs/collector.md / pruner + codegen CLIs)
```
