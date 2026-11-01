# ASIM sample and parser inventory

Status: research evidence; static analysis only, no parser executed
Reviewed: 2026-11-01
Canonical for: which public ASIM samples and parsers can become reference fixtures for Jev schema and field-mapping evaluation

The aim is to test Jev's schema-level predictions and our field-mapping
predictions against Microsoft's own parsers on as many sources as possible, to
expose edge cases before blind decisions on in-house logs. The current
reference fixtures cover five source families. Microsoft's Azure-Sentinel
repository publishes many more ASIM parsers, often beside an ingested sample and
sometimes a raw log export, a table schema, or contributor tester output.

Both evaluations need a reference fixture: a pinned sample and parser whose
native output supplies the labels. Each emitted row's `EventSchema` labels
schema selection, and its normalized fields label mappings. This inventory is
the selection step. It lists which published sample and parser pairs for
Authentication, NetworkSession and AuditEvent could become fixtures, and what
each would need. It runs neither Jev nor any parser. Other schemas can be added
later through the same command.

The inventory is not a new data source. It reads the public repository at one
immutable commit, verifies every file against its Git blob SHA, and stores the
bytes in the ignored corpus cache used by the benchmark runner, like other
third-party inputs. Nothing upstream is copied into this repository.

## Reproduce

```powershell
uv run asim-forge reference inventory --revision 2985095e602f32c493e43998856ac9bcf1382afd --output artifacts/asim-inventory
```

The command writes `inventory.json` and `inventory.md`. Pass `--schema` to
choose schemas. Without `--revision`, the current `master` head is resolved to
a commit SHA. Set `GITHUB_TOKEN` to avoid anonymous API limits. A repeated run
reuses verified cached bytes.

## Method

The command lists `Sample Data/ASIM` and each schema's `Parsers/ASim<Schema>`
directory through the Git tree API. It fetches parser YAML, tester exports,
table-schema files, and only those sample files whose names refer to a
requested schema.

- **Parsers.** Source parsers (`ASim<Schema><Product>`) are read from their
  YAML. A KQL lexer ignores comments and strings, then records source tables,
  helper functions, parameters, and literal vendor and product values. Schema
  unions and empty parsers are skipped. Each `vim` filtering variant is attached
  to its source parser.
- **Samples.** Each ingested CSV is profiled for column fill, source tables
  (the `Type` column), device identity columns, populated normalized fields, and
  a raw companion with the same file stem.
- **Pairing.** A sample pairs with a parser in the same schema when every
  sample table is read by that parser. A table read by several parsers, such
  as `Syslog` or `CommonSecurityLog`, also requires identity evidence. That
  evidence is either the parser's vendor and product in the sample path, or
  the sample's `DeviceVendor` and `DeviceProduct` among the parser's literals.
- **Readiness.** A pairing is `ready` when static analysis finds nothing beyond
  the sample and parser that local native execution would need. It is
  `needs-setup` when the blockers below need a fixture author's attention, and
  `excluded` when the input is normalized or a duplicate.

| Blocker | Meaning |
| --- | --- |
| `unsupported-parameters` | The parser takes parameters other than `disabled`. |
| `column-types` | No upstream table-schema file declares the source columns. |
| `missing-tables` | The parser reads tables that the sample does not contain. |
| `unknown-source-table` | The sample has no populated `Type` column. |
| `ambiguous-parser` | More than one parser matches the sample. |
| `source-setup` | The parser filters through a watchlist or `_ASIM_GetSourceBySourceType`. |
| `normalized-input` | The sample is already normalized (an `ASim*Logs` table or populated `EventSchema`). |
| `duplicate-sample` | Another sample has at least 90% row similarity. |

`needs-setup` is achievable work, not a dead end. Four existing fixtures fall
into it: OpenSSH, and the Cisco ASA, Cisco Meraki and Carbon Black Cloud
NetworkSession fixtures. Each already executes locally, using declared column
types and a `source-setup.kql` that stubs watchlist helpers or declares empty
tables for unions the sample does not contain.

## Results at `2985095`

| Schema | Source parsers | Parsers with a sample | Samples | Pairings | Ready | Ready, raw text | Needs setup | Excluded |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Authentication | 50 | 32 | 35 | 36 | 8 | 4 | 24 | 4 |
| NetworkSession | 42 | 20 | 25 | 23 | 9 | 2 | 13 | 1 |
| AuditEvent | 24 | 17 | 20 | 20 | 11 | 4 | 6 | 3 |
| Total | 116 | 69 | 80 | 79 | 28 | 10 | 43 | 8 |

A sample paired with two parsers contributes two pairings. The 28 ready
pairings cover 28 parsers from 13 vendors, compared with five source families
in the current fixtures.

Input shape determines what a blind evaluation can show:

| Schema | Raw text | Raw companion | Structured only | Normalized |
| --- | ---: | ---: | ---: | ---: |
| Authentication | 11 | 3 | 20 | 1 |
| NetworkSession | 7 | 6 | 12 | 0 |
| AuditEvent | 5 | 3 | 11 | 1 |

Raw text means a free-text message column at least 50% populated. A raw
companion is a separate `_RawLogs` export. Structured-only samples, 43 of the
80, contain connector-parsed columns and no original message.

Blocker counts over all pairings were: unsupported parameters 33, column types
25, missing tables 15, duplicate sample 6, ambiguous parser 4, unknown source
table 2, normalized input 2, and source setup 2. The command also read 43
table-schema files and 195 tester exports. It skipped six schema unions and
three empty parsers.

## What Jev and mapping evaluation can use now

Reference preparation turns one message column per row (the fixture's
`message_field`) into the events that Jev and the mapping approaches see. Only
raw-text pairings fit that today, so the useful headline is 10 ready pairings,
not 28.

| Schema | Parser | Rows | Relation to current fixtures |
| --- | --- | ---: | --- |
| Authentication | `ASimAuthenticationCiscoASA` | 217 | Existing fixture |
| Authentication | `ASimAuthenticationCiscoMeraki` | 18 | Existing fixture |
| Authentication | `ASimAuthenticationPaloAltoCortexDataLake` | 2 | New; too small alone |
| Authentication | `ASimAuthenticationVectraXDRAudit` | 47 | New |
| NetworkSession | `ASimNetworkSessionBarracudaWAF` | 15 | Existing fixture |
| NetworkSession | `ASimNetworkSessionCheckPointFirewall` | 41 | New |
| AuditEvent | `ASimAuditEventBarracudaWAF` | 15 | Replacement sample for an existing fixture |
| AuditEvent | `ASimAuditEventCiscoMeraki` | 21 | Existing fixture |
| AuditEvent | `ASimAuditEventInfobloxBloxOne` | 20 | New |
| AuditEvent | `ASimAuditEventVectraXDRAudit` | 42 | New |

All ten read their text from `Message`. Another 15 raw-text pairings need setup.
Three of those are existing fixtures that already run with a `source-setup.kql`
(OpenSSH, and Cisco ASA and Cisco Meraki NetworkSession), and one is the
replacement Barracuda WAF Authentication sample. The other 11 come from Cisco
ISE, Cisco FTD, Palo Alto, VMware ESXi and Zscaler ZPA parsers, and two shared
samples among them also carry the `ambiguous-parser` blocker.

The 12 raw-companion and 34 structured pairings that are ready or need setup
cannot reach Jev until their representation is decided. Each fixture also holds
a single schema, so one fixture alone rewards a constant answer. Results must be
pooled across fixtures, as in the
[Jev schema experiment](../jev-schema-experiment.md).

## Edge cases found

- **Duplicates.** Seven samples repeat another sample. CrowdStrike Falcon Data
  Replicator and Salesforce samples are published both with and without a
  table-name suffix, and SonicOS repeats SonicWall Firewall. Near-duplicates
  share 91–93% of their rows. For each group, the inventory keeps one copy,
  preferring a paired sample.
- **Column exports named like samples.** Two `CommonSecurityLog_NetworkSession.csv`
  files, at the root and under `SonicWallFirewall/`, list table columns rather
  than log rows. The inventory recognises the header and reads them as table
  schemas.
- **Samples without a parser.** Three samples remain unpaired. Cynerio's
  Authentication and NetworkSession samples have no parser that reads their
  table. SonicOS uses a shared table without vendor identity evidence.
- **Ambiguity.** Cisco ISE and Cisco ISE Administrator parsers both match
  `cisco_ise_asim_auth.csv`. The PAN-OS Authentication sample has an empty
  `Type`, so GlobalProtect and PAN-OS parsers both match it. These need a person
  to decide which parser the sample was meant for.
- **Label leakage.** Ten samples already contain populated normalized fields,
  most often `EventType`, `EventVendor` or `EventProduct`. 26
  samples have schema words such as `audit`, `auth`, `login`, `network`, `flow` or
  `logon` in their table names.
- **Upstream naming errors.** `vimAuditEventInfbloxBloxOne` does not mirror its
  source parser's name and is matched through `EquivalentBuiltInParser`. Tester
  exports named `Baracuda_*` and `VMwareCarbonBackCloud_*` cannot be matched to
  a parser by name, and Cynerio's exports have no parser to match.
- **Tester exports.** Exported `ASimDataTester` results are dominated by
  `EventProduct` (44) and `EventVendor` (40) enumeration errors, plus
  `DvcHostname` format errors (10). The enumeration errors are pervasive enough to
  suggest a stale tester enumeration rather than parser faults. These exports
  describe the contributor's workspace at the time, not the published sample bytes.
- **Fixture drift.** Five pinned fixture parsers changed upstream after their
  pinned revisions: Barracuda WAF Authentication and AuditEvent, Cisco Meraki
  AuditEvent and NetworkSession, and Carbon Black Cloud AuditEvent. Upstream
  also replaced both Barracuda WAF samples those fixtures pin. The pinned paths
  no longer exist, and the new `Barracuda_WAF_Authentication_IngestedLogs.csv`
  and `Barracuda_WAF_AuditEvent_IngestedLogs.csv` have different bytes. The
  inventory reports such paths as `missing`. The other pinned samples, the
  pinned table schema and the three corpus raw logs are unchanged. The fixtures
  remain valid at their pins. A refresh would be a deliberate, separately
  reviewed change.

## Interpretation and blind-input rules

A pairing is silver research evidence. It shows that a parser reads the
sample's table with matching identity evidence. It does not show that the
parser's output is correct, that the sample is representative, or that the
schema assignment is semantically right. The
[ASIM agent principles](asim-agent-principles.md) keep schema meaning, field
roles and output conformance separate. The same separation applies here.

When these samples feed blind schema or mapping decisions, provider inputs must
exclude sample and parser file names, source table names, and populated
normalized fields. Each of these reveals the expected answer. Structured-only
samples need a representation decision before they can stand in for raw
in-house logs. That decision is still open.

## Next stages

1. Turn the five new ready raw-text pairings into reference fixtures with the
   existing fixture contract, and capture their native output locally. Assign
   them to the frozen split before tuning anything.
2. Run Jev reference agreement and `reference compare` pooled across all
   fixtures, and record where predictions disagree with the parsers.
3. Work through raw-text `needs-setup` pairings, starting with declared column
   types, empty stub tables, and parameter defaults.
4. Decide how raw-companion and structured samples are presented to Jev and the
   mapping approaches, then add tester-style conformance checks on captures,
   reusing catalogue checks and keeping conformance separate from schema
   correctness.

Automatic parser creation remains out of scope until these stages show where
existing parsers and samples disagree.
