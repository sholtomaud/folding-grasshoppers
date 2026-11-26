# Proof Attestation Changelog

Append-only cryptographic proof ledger. Each entry chains its `attestationSha256` to the previous entry so editing or removing historical proof records breaks the chain.

## 2026-11-26T01:42:12Z — `68696c5` (PASS)

- **Commit:** `68696c5`
- **Timestamp:** `2026-11-26T01:42:12Z`
- **Proof Lock Root SHA-256:** `6c58d0d761054d1a8b3faed55409219930499d83f5dc629289d550778602f123`
- **Verdict:** `PASS`
- **Summary:** Milestone 1 follow-up: Resources layout, README, audit fixes; harness changes need allow-harness-change
- **Previous Attestation:** `0000000000000000000000000000000000000000000000000000000000000000`
- **Attestation SHA-256:** `f08a858184b303bf582aef6234514c7b26c86e99ef96ca36becff0b59f7eef64`

## 2026-11-26T02:16:42Z — `c1d882c` (PASS)

- **Commit:** `c1d882c`
- **Timestamp:** `2026-11-26T02:16:42Z`
- **Proof Lock Root SHA-256:** `1b8a93fda3608e6c329b73cc81c37ce936b4abc3552752e27bd7cd13deb8c766`
- **Verdict:** `PASS`
- **Summary:** Run Python/uv/Node in the dev container: verify.sh dispatches to scripts/tools.sh when uv is absent; CI builds the macOS runtime in cross mode
- **Previous Attestation:** `f08a858184b303bf582aef6234514c7b26c86e99ef96ca36becff0b59f7eef64`
- **Attestation SHA-256:** `f6f554413bacc9cd716cd97e8e477dd929db5ef45440a3542388088663a54162`

## 2026-11-26T02:35:32Z — `b0ae35b` (PASS)

- **Commit:** `b0ae35b`
- **Timestamp:** `2026-11-26T02:35:32Z`
- **Proof Lock Root SHA-256:** `ab634e9fa99b844982f4c9ad2250a19424378869bfad2c71c55eff85e742e9ba`
- **Verdict:** `PASS`
- **Summary:** verify.yml: re-run on labeled/unlabeled so allow-* labels take effect
- **Previous Attestation:** `f6f554413bacc9cd716cd97e8e477dd929db5ef45440a3542388088663a54162`
- **Attestation SHA-256:** `38562ae6685aab9bb3f637ed4a7a0f1f15546e6a5332cee35d4e0508a1727319`

## 2026-11-26T05:09:38Z — `3b40ed6` (PASS)

- **Commit:** `3b40ed6`
- **Timestamp:** `2026-11-26T05:09:38Z`
- **Proof Lock Root SHA-256:** `c7a1bef58fd2e046b286a85307aee20b04eb32066d2e1ee4dac1caffeb8d1633`
- **Verdict:** `PASS`
- **Summary:** Hops activity endpoint + status bar; raise floors (pytest 64, swift 58)
- **Previous Attestation:** `38562ae6685aab9bb3f637ed4a7a0f1f15546e6a5332cee35d4e0508a1727319`
- **Attestation SHA-256:** `6392bac427066477687d370a0d5e80e1ee5ea9590b99022b494aa30e3aac12de`

## 2026-11-26T08:13:17Z — `4291ba9` (PASS)

- **Commit:** `4291ba9`
- **Timestamp:** `2026-11-26T08:13:17Z`
- **Proof Lock Root SHA-256:** `409644a6429eaaf348a1f662c221eaafa814ea682da9af54628c5674e6f842b0`
- **Verdict:** `PASS`
- **Summary:** Geometry milestone PR 1: /waffle_slice and /export (DXF/SVG), parts document, pyclipper dependency; new tests, pytest floor 64 -> 122
- **Previous Attestation:** `6392bac427066477687d370a0d5e80e1ee5ea9590b99022b494aa30e3aac12de`
- **Attestation SHA-256:** `5cc857272b0b67acd3989f34f1a411a2bb21edd98f5e2b20cf6c60d4fd95ff50`

## 2026-11-26T08:16:15Z — `c67d871` (PASS)

- **Commit:** `c67d871`
- **Timestamp:** `2026-11-26T08:16:15Z`
- **Proof Lock Root SHA-256:** `90366b81b0082123edbcbf8dff32a825a0b5f54110b9f2a28157de410980f771`
- **Verdict:** `PASS`
- **Summary:** Geometry milestone PR 2: /contour_stack with neighbour-outline etching; pytest floor 122 -> 142
- **Previous Attestation:** `5cc857272b0b67acd3989f34f1a411a2bb21edd98f5e2b20cf6c60d4fd95ff50`
- **Attestation SHA-256:** `33a0a6a05dc0aa014d57024fa89ee5aed64743aaa7352d3fb5d7744a7a7fe818`

## 2026-11-26T08:23:34Z — `ee1c368` (PASS)

- **Commit:** `ee1c368`
- **Timestamp:** `2026-11-26T08:23:34Z`
- **Proof Lock Root SHA-256:** `02b77938cd62ebee8439aab6e7170e006ae8efaed619d0826e849c11d55f47b8`
- **Verdict:** `PASS`
- **Summary:** Geometry milestone PR 3: multi-sheet /nest (true-shape bottom-left fill with no-fit polygons); pytest floor 142 -> 166
- **Previous Attestation:** `33a0a6a05dc0aa014d57024fa89ee5aed64743aaa7352d3fb5d7744a7a7fe818`
- **Attestation SHA-256:** `e67dbed844d74b3a0543ec23683421804f4d8d7613342941e96a8d4e357c6d6c`

## 2026-11-26T08:26:28Z — `659e68d` (PASS)

- **Commit:** `659e68d`
- **Timestamp:** `2026-11-26T08:26:28Z`
- **Proof Lock Root SHA-256:** `943d0156ee4514a590d6f6246a0246d7038311ea6bdf3317a8b12a81611b5089`
- **Verdict:** `PASS`
- **Summary:** Geometry milestone PR 4: /unfold (nets, folds, glue tabs, edge numbers); part texts in DXF/SVG; pytest floor 166 -> 191
- **Previous Attestation:** `e67dbed844d74b3a0543ec23683421804f4d8d7613342941e96a8d4e357c6d6c`
- **Attestation SHA-256:** `6ff93bbfbc05d1e96bda82e0fefcde494bcdceb38c5edde210ce2fdcfd1a6035`

## 2026-11-26T21:33:03Z — `7ea4671` (PASS)

- **Commit:** `7ea4671`
- **Timestamp:** `2026-11-26T21:33:03Z`
- **Proof Lock Root SHA-256:** `ee0a8c80899cd26a5bd88732f2255e1280e49402464accf8e4aad24d60ff2823`
- **Verdict:** `PASS`
- **Summary:** Design Assistant: new engine tests (shapes, OBJ/STL import, triangulation, design tools, preview routes) and Swift tests (JSONValue, AnthropicClient, DesignAssistant loop, design client, secret store); raised floors to pytest 265, swift 89
- **Previous Attestation:** `6ff93bbfbc05d1e96bda82e0fefcde494bcdceb38c5edde210ce2fdcfd1a6035`
- **Attestation SHA-256:** `be140f3140bfe652cbc897b3c8df8d3c3ff5b0197af7ba653171f1bfd63cb85e`
