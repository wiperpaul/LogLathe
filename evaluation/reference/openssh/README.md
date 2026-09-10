# OpenSSH ASIM reference fixture

This is a pinned functional regression fixture, not independently adjudicated gold.
It contains 20 public post-ingestion Syslog rows, an upstream ASIM parser and three
helpers, and native reference outputs for those rows plus ten controlled variants.
The generated variants are defined in `asim_forge.reference.fixtures` and retain
their parent event IDs. All events belong to one source family.

See [the pilot guide](../../../docs/reference-pilot.md) for review, capture, replay,
and the exact evidence boundary.

`manifest.json` records upstream paths, immutable revision, byte checksums, source
column types, target version, and attribution. Preserve the original resource
bytes. `reference-output.json` is a native Kusto capture, with a checksum sidecar;
its query and input hashes are verified by the offline test suite. Expected output
does not enter an ordinary mapping request or the initial cluster-review page.

Upstream resources are from Microsoft Azure-Sentinel under the MIT licence in
`LICENSE.upstream`. Reference output is implementation-derived evidence; upstream
schema and value disagreements must remain visible for review.
