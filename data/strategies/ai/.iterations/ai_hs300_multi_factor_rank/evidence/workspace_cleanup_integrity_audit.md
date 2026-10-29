# Workspace Evidence Integrity Audit

This audit reads existing evidence and SHA-256 sidecars only. It does not rewrite,
repair, or delete research evidence. The audit script's own sidecar is included;
this report's sidecar is created after the report and is therefore not in this count.

- Sidecars checked: 911
- Matching: 904
- Mismatching: 4
- Empty: 1
- Missing targets: 2

## Existing exceptions

- `r270_preregistration.sha256` (missing): target=r270_preregistration; expected=86377e88bb9f7ead1c8b874cc977d4e66b2f5a01efb24ee779a26d93e3f4242e
- `r271_preregistration.json.sha256` (mismatch): target=r271_preregistration.json; expected=29eb20fa35793a02ca288da5b6edf6c57b07514b6733f7d66dcce5843586b908; actual=ce1d3edc7a61792bf3fb9065cb003f725e4b6a8476745b134b6c97e6b1a5b87a
- `r279_preregistration.sha256` (missing): target=r279_preregistration; expected=2faed5b7153c6e237499d464931a84ca114420df6b2d1a40c71742f53eac47cd
- `r304_A12_final_record_addendum.py.sha256` (mismatch): target=r304_A12_final_record_addendum.py; expected=b99cf1d7b9c6d82f46501ba992045692e3c53da146c2f0a39b9534fddf9631eb; actual=c8bee77c797290f8028575f6a22cbe745dcb7d3347ebafaea32ca14a5c68e085
- `r304_official_archive/attachments/14497/notice/20221207152016-指数样本调整名单.xlsx.sha256` (empty): target=(not specified); expected=(empty)
- `r304_official_archive/request_receipts/attachment-1006022-1002403.json.sha256` (mismatch): target=attachment-1006022-1002403.json; expected=fa569b71fcca26770eeea41dbb9318433a2d59ac7df840ae42bc08f96e204c3c; actual=1e89aaad0214787b83ffdcdd7397d0679ee38640aea8f843e03879e225183741
- `r304_preregistration_amendment_A12.json.sha256` (mismatch): target=r304_preregistration_amendment_A12.json; expected=50b386858f7e2a81bdcf71cbaae4b2ded9a82501d8720f959e121fce10d94567; actual=82e38f23f30ebb806fde5e906390cbc74c396d22936d1b1705de44e3e29dab29

These exceptions are retained as found. Hashes were not regenerated and the
associated evidence files were not modified to make the checks pass.
