# Workspace Evidence Integrity Audit

This audit reads evidence and SHA-256 sidecars. It does not repair mismatched
sidecars or rewrite research results. The report sidecar is created after this scan.

- Audit date: 2026-10-30
- Sidecars checked: 914
- Matching: 910
- Mismatching: 4
- Empty: 0
- Missing targets: 0

## Publication path normalization

Frozen evidence files were preserved byte-for-byte. Portable copies replace local
checkout paths with repository-relative paths and have independent SHA-256 sidecars.

- `r272_operating_margin_improvement_corrected.portable.json`: previous SHA-256 `94496e3f1488d143a14a4ff0171ee0ffc35e636e217d61bb23777baed9e733cc`; current SHA-256 `c67ba2f59d86ea7c5bd7f344c61b618ffc6f50e06757dc3b2f5c6184fb96b946`.
- `r289_preregistration.portable.json`: previous SHA-256 `f7e4430f7dbcf2a30299e6ddac1841fbe837d613826449fd9f508521c3ed5a60`; current SHA-256 `b565210ce7ef066519ad71e9637f396740c1e83678e1ea574779080bfa88e077`.
- `r304_a5_browser_state.json`: previous SHA-256 `f036b17cf698c5c404b62871ce3ded054ee182938f471928cc27d6a1616b40b4`; current SHA-256 `f036b17cf698c5c404b62871ce3ded054ee182938f471928cc27d6a1616b40b4`.

The earlier `workspace_cleanup_integrity_audit.md` is retained as its original
snapshot. Its two missing targets (`r270_preregistration` and
`r279_preregistration`) are present in this scan and resolve to the corresponding JSON files.

The empty sidecar for the R304 notice 14497 workbook was removed: its request
record confirms that the workbook was not present locally and the sidecar had no digest.

## Existing exceptions

- `r271_preregistration.json.sha256` (mismatch): target=r271_preregistration.json; expected=29eb20fa35793a02ca288da5b6edf6c57b07514b6733f7d66dcce5843586b908; actual=ce1d3edc7a61792bf3fb9065cb003f725e4b6a8476745b134b6c97e6b1a5b87a
- `r304_A12_final_record_addendum.py.sha256` (mismatch): target=r304_A12_final_record_addendum.py; expected=b99cf1d7b9c6d82f46501ba992045692e3c53da146c2f0a39b9534fddf9631eb; actual=c8bee77c797290f8028575f6a22cbe745dcb7d3347ebafaea32ca14a5c68e085
- `r304_official_archive/request_receipts/attachment-1006022-1002403.json.sha256` (mismatch): target=attachment-1006022-1002403.json; expected=fa569b71fcca26770eeea41dbb9318433a2d59ac7df840ae42bc08f96e204c3c; actual=1e89aaad0214787b83ffdcdd7397d0679ee38640aea8f843e03879e225183741
- `r304_preregistration_amendment_A12.json.sha256` (mismatch): target=r304_preregistration_amendment_A12.json; expected=50b386858f7e2a81bdcf71cbaae4b2ded9a82501d8720f959e121fce10d94567; actual=82e38f23f30ebb806fde5e906390cbc74c396d22936d1b1705de44e3e29dab29

The listed exceptions are retained as found. Their sidecars and associated
evidence files were not changed to make the checks pass.
