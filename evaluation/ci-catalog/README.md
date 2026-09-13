# Pinned ASIM catalogue for offline CI

This is the unchanged `ASIM/dev/ASimTester/ASimTester.csv` snapshot from
[Azure-Sentinel](https://github.com/Azure/Azure-Sentinel) at commit
`027a0f9338bfabcb27b784571b771c54572ebf01`. Its manifest records the
source revision and SHA-256 digest; `load_catalog` verifies both the file hash and
parsed field counts before a benchmark uses it. The upstream MIT licence is in
`LICENSE.upstream`.

This snapshot keeps automatic corpus checks independent of catalogue downloads.
To update it, intentionally sync a new upstream commit, review the catalogue
changes, and replace both the CSV and manifest together.
