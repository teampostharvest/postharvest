# shared/ — cross-language contract artifacts (finalplanv2.md §6, §12)
#
# This directory is the ONE place the three services agree on shapes. If a
# shape changes, it changes here first, and every service's Phase 1 JSON
# types + test suites are updated against it.

## proto/
`postharvest.proto` — canonical messages (`Post` = the 33-key schema,
`FetchRequest/FetchResponse`, `ParseRequest/ParseResponse`) with the
field-discipline rules embedded. Phase 1 speaks the same shapes over
HTTP + JSON; Phase 2 generates typed gRPC stubs from this file.

## fixtures/
Golden request/response payloads used by each service's test suite to prove
it can parse the current contract (a shape change that breaks a golden
fixture fails CI in the language that broke it):

- `fetch_response.html.json` — a `FetchResponse` for a small HTML page
- `parse_response.posts.json` — a `ParseResponse` with one full 33-key `Post`

## Editing rules
1. Never renumber/reuse a proto field number (comment block in the .proto).
2. Update the JSON fixtures whenever the contract changes.
3. Phase 1 uses these JSON shapes verbatim (field names == proto field names;
   `null` for missing scalars until gRPC, where defaults apply).