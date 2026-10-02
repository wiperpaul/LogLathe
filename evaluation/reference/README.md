# Mixed ASIM parser reference fixtures

Status: functional fixtures; no new Jev quality measurement yet
Audience: evaluators and parser developers
Canonical for: fixture selection, source-family split, and native coverage
Last verified: 2026-10-01

These small, attributed Microsoft sample exports and exact parsers are pinned to
Azure-Sentinel `027a0f9338bfabcb27b784571b771c54572ebf01`. Each directory includes
resource hashes, the upstream MIT licence, and a checked native Kusto capture.
They reuse the [reference workflow](../../docs/reference-pilot.md); normal tests
verify captures offline without Docker, downloads, or a provider key.

## Frozen fixture split

`mixed-split.json` uses the existing `SemanticDatasetSplit` contract at **fixture
granularity**: its `case_id` is a fixture ID, not an annotation task ID. It is a
source-family assignment plan, not an argument to the case-level `schema-rank
--split` command. When creating a case-level evaluation, inherit each source
family's partition for every template, event, and derivative. Never randomly
divide rows or allocate different schemas from one product to different splits.

| Partition | Source | Intended sample schema | Public inputs | Inputs with native output |
| --- | --- | --- | ---: | ---: |
| train / development | OpenSSH | Authentication | 20 | 20 |
| validation | Cisco Meraki | Authentication | 18 | 18 |
| validation | Cisco Meraki | NetworkSession | 13 | 13 |
| validation | Cisco Meraki | AuditEvent | 21 | 21 |
| test | Barracuda WAF | Authentication | 15 | 15 |
| test | Barracuda WAF | NetworkSession | 15 | 15 |
| test | Barracuda WAF | AuditEvent | 15 | 15 |

Total: 117 public input rows in three source families. OpenSSH's ten controlled
variants are additional regression probes, excluded from this public-row count.
The new fixtures have no generated variants. Only OpenSSH has undergone human
cluster review. The [Meraki validation trial](../../docs/research/jev-meraki-assessment-2026-10-02.md)
is complete using unreviewed clusters; it compared 32 templates and 49 of the 52
inputs represented in their examples. Barracuda test predictions remain reserved.

The test source supplies a balanced 45-event, three-schema comparison, and the
validation source supplies 52 events across the same three schemas. Report
coverage and per-schema agreement separately; do not treat any future dropped
input as Unsupported or infer its label from a filename. Three source families
are still insufficient for a broad generalization claim, and public
samples may have appeared in a provider's training data.

Freeze model definitions before looking at test predictions. Keep test results
out of prompt tuning; if they motivate changes, mark this version as inspected
development evidence and choose a new test source. Source inspection to establish
capture integrity is not blind independent semantic adjudication.

## Ingestion and upstream limitations

CSV bytes are unchanged. Format-2 manifests explicitly map export headers such as
`TimeGenerated [UTC]` to their source-table names and declare source types. Meraki
exports use three timestamp formats, recorded per fixture; Barracuda numeric `_d`
columns are real.
Messages go to clustering unchanged; additional CSV metadata, filenames, declared
schemas, and native outputs must not enter a model's source-message prompt.

Meraki Authentication and AuditEvent samples use `meraki_CL.Message` and their
matching custom-table parsers. NetworkSession uses `Syslog.SyslogMessage` and the
Syslog parser variant. Its pinned `source-setup.kql` supplies
`_ASIM_GetSourceBySourceType` with the sample computers registered as CiscoMeraki
sources. This is explicit local source configuration, not a test of Sentinel's
watchlist service. It supplies no event labels and changes no source messages.
All normalization helpers and parser bodies are exact upstream bytes.

Split version 2 replaces Cisco ISE before any new live Jev trial. The ISE sample
sets yielded 0/30 Authentication, 1/20 NetworkSession, and 20/20 AuditEvent outputs.
On 2026-10-01, all three parser files and all three sample files were compared
byte-for-byte with upstream head `cfb3c3ded5cb65c907abb6b71b28b9ed25806ee2` and were
unchanged. Updating the pin therefore would not fix this sample/parser mismatch.
ISE is excluded from the active fixture set; we did not repair its inputs, tune
against its scores, or start an upstream issue investigation. The previous local
captures remain in ignored artifacts for provenance.

Barracuda uses already ingested `barracuda_CL` columns. Its Authentication parser
also references `CommonSecurityLog`; `source-setup.kql` explicitly supplies that
alternate table empty, with a sufficient string-column shape for compilation.
This tests the custom-table branch, not CEF ingestion. NetworkSession's export
omits `_ItemId`; its setup binds an empty string, leaving `EventUid` empty. Both
setup files are local fixture code, hash-pinned into the native program and query
identity. They do not fabricate source records or schema labels.

## Prepare review and reproduce native output

Use the same commands as OpenSSH, for example:

```console
uv run asim-forge reference prepare evaluation/reference/cisco-meraki-auditevent --output artifacts/mixed-reference/cisco-meraki-auditevent
```

Repeat for each fixture in the split. Every prepared bundle contains the ordinary
DeepParse build and Potato review task. Review coherence before promoting its
clusters; do not generate fake approval files to satisfy the annotation queue.
For a source-family clustering experiment, combine that family's message columns
before clustering and retain a sidecar mapping each line to fixture ID/source row.
Separate per-fixture preparation does not test whether clustering merges events
across the sample-schema files.

To independently execute a fixture, follow the existing local Kusto startup and
`reference capture` instructions, writing a new capture under `artifacts/`. All
six new checked captures used the same pinned engine image as OpenSSH. Preserve
the checked reference snapshot; compare newly captured outputs separately.
The [Jev replay recipe](../../docs/jev-schema-experiment.md#preserve-and-replay-a-reported-trial)
and existing release policy own any future trial archive. No additional publisher,
automatic download, or paid experiment is introduced here.
