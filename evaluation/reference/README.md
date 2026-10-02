# Mixed ASIM parser reference fixtures

Status: functional fixtures; no new Jev quality measurement yet
Audience: evaluators and parser developers
Canonical for: fixture selection, source-family split, and native coverage
Last verified: 2026-10-02

These small, attributed Microsoft sample exports and exact parsers are pinned to
the Azure-Sentinel revisions declared in each manifest. The original fixtures use
`027a0f9338bfabcb27b784571b771c54572ebf01`; the Carbon Black and Cisco ASA additions
use `4fc25fd4c008337dfd6d9c5663be833e66575d53`. All use the existing catalogue pin
`027a0f9338bfabcb27b784571b771c54572ebf01` and matching schema versions. Each directory includes
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
inputs represented in their examples. The subsequent
[Barracuda test trial](../../docs/research/jev-barracuda-assessment-2026-10-02.md)
used unchanged definitions and compared ten templates representing 26 of its 45
inputs. Its predictions are now inspected; its clusters remain unreviewed.

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

## Additional event-text sources

`expanded-split.json` is a separate version-3 source-family plan. The original
version-2 plan and its reported trials remain unchanged. Version 3 assigns the
already inspected Barracuda family to validation, keeps OpenSSH in development
and Meraki in validation, and reserves **both Carbon Black Cloud and Cisco ASA
for test**. No live Jev requests have been made for these additions. Inherit these
partitions when making case-level splits; the fixture plan itself is not a
case-level `schema-rank --split` input.

| New source | Sample schema | Public inputs | Inputs with native output | Nonblank event text | Prepared clusters |
| --- | --- | ---: | ---: | ---: | ---: |
| Carbon Black Cloud | Authentication | 16 | 16 | 16 | 5 |
| Carbon Black Cloud | NetworkSession | 32 | 32 | 16 | 5 |
| Carbon Black Cloud | AuditEvent | 66 | 66 | 66 | 34 |
| Cisco ASA | Authentication | 217 | 217 | 217 | 15 |
| Cisco ASA | NetworkSession | 30 | 27 | 30 | 4 |

The additions supply 361 public rows and 345 nonblank messages. Across both
fixture plans there are now 478 distinct public rows in five source families.
Native output is **official-parser reference evidence**, not independent semantic
gold or a measurement of Jev quality. All new clusters remain unreviewed, and
per-fixture cluster counts do not measure mixed-schema clustering quality.

Carbon Black uses the original `description_s` for Authentication/AuditEvent and
`event_description_s` for NetworkSession. These are vendor-provided narrative
descriptions in ingested exports, not the complete raw records. The NetworkSession
descriptions contain vendor markup, preserved as written. Its first 16 rows have
no description: native capture covers all 32, while clustering consumes only rows
17–32. Blank lines preserve the original source row identity. Do not manufacture
messages from the structured columns or include those 16 missing-text rows in a
text classifier's denominator. Report text coverage and representative-example
coverage separately.

Carbon Black's original `source.csv` remains byte-for-byte upstream content.
`ingested-source.csv` is an explicit adapter that adds only an empty `_ItemId`
system column; every original cell, including message text, is unchanged. The
parser's `project-rename` requires a column rather than a scalar binding. To
reproduce this adapter, read `source.csv` with `csv.DictReader` using UTF-8 with BOM
handling, append `_ItemId` to its fieldnames, and write each unchanged row plus
`{"_ItemId": ""}` using `csv.DictWriter`. Both inputs are hash-pinned, and offline
tests check their exact cell relationship. `EventUid` stays empty. NetworkSession's
setup supplies the optional `CarbonBlackNotifications_CL` table empty, using the
parser's declared shape; this tests endpoint events without notification enrichment.
Boolean `_b` columns retain their Boolean type, and timestamp exports allow absent
fractional seconds when the manifest declares a fractional-seconds format.

Cisco ASA uses the original `CommonSecurityLog.Message` strings, including ASA
message IDs, rather than reconstructed CEF records. Its column types come from
the pinned upstream `CommonSecurityLog_Schema.csv`; empty datetime cells are null.
Its setup leaves the omitted `_ItemId` empty and supplies an empty NetworkAddresses
watchlist for NetworkSession. Thus watchlist-based interface enrichment is not
tested. The NetworkSession parser emits nothing for rows 4, 15 and 21: the exported
`DeviceEventClassID` values (113004, 113005 and 611102) differ from the IDs embedded
in their messages (710003, 106001 and 106007). These inputs are retained unchanged;
they are **not_selected**, not Unsupported or implicitly NetworkSession-labelled.
The other 27 rows provide native schema comparisons.

The official sample inventory also contains CrowdStrike Falcon, SentinelOne and
Illumio Core across all three schemas. They are deferred for this unstructured
experiment: CrowdStrike's ingested `Message` cells are empty; SentinelOne mixes
activity descriptions with structured alerts and threats; Illumio's exports are
structured fields without a narrative message. Their existence alone does not
make them suitable inputs for the current text clustering and review workflow.

Official sources: [Carbon Black Authentication samples](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/Sample%20Data/ASIM/VMware_CarbonBlackCloud_ASimAuthentication_IngestedLogs.csv),
[NetworkSession samples](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/Sample%20Data/ASIM/VMware_CarbonBlackCloud_ASimNetworkSession_IngestedLogs.csv),
[AuditEvent samples](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/Sample%20Data/ASIM/VMwareCarbonBlackCloud_ASimAuditEvent_IngestedLogs.csv),
[Cisco ASA Authentication samples](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/Sample%20Data/ASIM/Cisco_ASA_Authentication_IngestedLogs.csv),
[NetworkSession samples](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/Sample%20Data/ASIM/Cisco_ASA_NetworkSession_IngestedLogs.csv).
Matching parsers and helper paths are recorded in each fixture manifest.

## Ingestion and upstream limitations

Upstream CSV bytes are unchanged; Carbon Black's additional adapter is described
above. Format-2 manifests explicitly map export headers such as
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
checked captures used the same pinned engine image as OpenSSH. Preserve
the checked reference snapshot; compare newly captured outputs separately.
The [Jev replay recipe](../../docs/jev-schema-experiment.md#preserve-and-replay-a-reported-trial)
and existing release policy own any future trial archive. No additional publisher,
automatic download, or paid experiment is introduced here.
