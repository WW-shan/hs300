# R296 证据索引

权威结论见 r296_result_v3.md 和 r296_isolated_build_attachment_audit_v3.json。首份报告、机器结果和两次事后更正均保留，没有覆盖。

面板候选 ID 48ca7b2f1105431d97c148e3e0574f4c 单次 POST/GET 均成功；数量 12→13，状态 pending。
计算前后核对 2624 个冻结输入文件和正式三层因子 6396 个分区，哈希一致。未调用 provider、builder main、回测、筛选或撮合，未读取收益/成交。

审计/登记脚本没有纳入原始事前脚本哈希冻结；本索引事后保存其哈希，并如实披露此限制。

| 文件 | SHA-256 |
| --- | --- |
| audit_r296_isolated_build_attachment.py | 2c1660887a00fab949b6ef023dc0a2a98bc98189850e3e73d05745980e33f7d3 |
| capture_r296_formal_factor_baseline.py | 5307f8abf275d70452bce13bafce136063d827ce5b09ed5946389fdecf545e7c |
| capture_r296_provenance.py | 366595a76cfdd19d29970a10e3204421d87c78707ae8ff3823e23c7e88c49fc0 |
| correct_r296_membership_eligibility.py | 363348ef0c9daf8c913afc3b5c2b0ffceb75c9dad0c501503ec4da097f2c115d |
| correct_r296_membership_interpretation.py | 3a588e3bf1be7e57f931448f0f12de3e5a3d5db97543ffed332c9f43dbef8206 |
| freeze_r296.py | 1acdb06862045d49c139b772f059d7eeaf61ebcd06d0ec4e369e152cf668cfa8 |
| freeze_r296.py.sha256 | 222a7cc5d38729152a32aede3f44b36af3923f80942684d2f748b5a5768371d4 |
| r296_formal_factor_baseline.json | 5859fc6059053257e0a9a38b10422433ddb16a1ded82d71922e62e1fcc98a287 |
| r296_formal_factor_baseline.json.sha256 | 3e5dbfcc2305ac076d17049ca500ad79df99fad79f15047fa4737c28e4d3a7d2 |
| r296_input_manifest.jsonl | 69f9d84709e8f68fa91297c6b54b5ce55ab598ca43104ca84200e168ac17410d |
| r296_input_manifest.jsonl.sha256 | 15ecf1e7850ba18a3af3627bd0ca49ff08ba7dd08e0cea40616dd389bf2bc516 |
| r296_isolated_build_attachment_audit.json | f76c8a697d19b9a44ae3da9d448c692cee310922e3a84f92b16745d7ef8d2399 |
| r296_isolated_build_attachment_audit.json.sha256 | 959b02e583b4d8084f7fd2ea21edcb8362714ae75dd1ea383ed6f519297e4916 |
| r296_isolated_build_attachment_audit_v2.json | 08ef207293c270deb1ee9f779561a7cd3ee28736f910913723bb7b0cfe57aa90 |
| r296_isolated_build_attachment_audit_v2.json.sha256 | 46eb27361e0433320ae4bd92eefb914374a25400562e5bb38dd688563d8c2fb0 |
| r296_isolated_build_attachment_audit_v3.json | 8b1e5faeb606b84bb3573b92b121979d707fea3b9374516e69b8aeee26e39684 |
| r296_isolated_build_attachment_audit_v3.json.sha256 | 10197d12a605e2120da67c3f53027c83ef6c8385e2b77150a202fd2d5a2b8e44 |
| r296_membership_eligibility_erratum.json | 5484f0f333ad26675ca4352da05d621ef0fd803dec41730bfa3e062e688ddd2c |
| r296_membership_eligibility_erratum.json.sha256 | 0b9b8853c0e951ead440ce038caf53cd1476a87e90ce096423eb10a43338820a |
| r296_membership_interpretation_erratum.json | 7ef01898d7a8a800dc3d8f59e02c15607227acc4b4282cff9eeef005533a5cac |
| r296_membership_interpretation_erratum.json.sha256 | efb3d29596d1133c766bd65ed09d9d6151e8cfeac7c9eb8e7bcf722b9e511e58 |
| r296_official_members.json | 63973e06447a99a7e1e11d367ce44ff48ebd4ef596e63a5c1cb4c65bc0df72b3 |
| r296_official_members.json.sha256 | f2d6f0dee11bf99bf9e0ca604dabcb32cdcbc0c6512076743d67de1bde8302c0 |
| r296_panel_registration.json | 2b6f6b3ac22e573fe773f3c1a4253671d7556528b03baffefbb69fb1c6bf8b92 |
| r296_panel_registration.json.sha256 | 3682e316cf2680b823a83498e67193b4d37bbc6f4685de8fd7d86c96a6a60b44 |
| r296_panel_registration_intent.json | f9bc1d1d2ebf95e1c944913870018d316557fb02e0551c7bffb772d50c3b0c5c |
| r296_panel_registration_intent.json.sha256 | 7d33685db78a4cafe20942ae098ea5696e594ed32f1dab8a5a212f1ad179cd7d |
| r296_panel_registration_post.json | 8db08e15049df7621b326290315e87ec24e52cfe351ab7928a1aef2c04086d5f |
| r296_panel_registration_post.json.sha256 | c797da1093a3915e6938aeb6648bf698966d91684749e35036eded48977c5444 |
| r296_preregistration.json | 1fe15e0365789dbec79e9c25f878f825886a04a3b07671f812c42afbc2780a12 |
| r296_preregistration.json.sha256 | baea571543a9288ecadfde6f79f72bc4c08c50ebd68f4d12668b883108f54599 |
| r296_result.md | ef09855e0f7be27f72296945f69da4f787a7f4461bc5e2fee9820a9913f0f70d |
| r296_result.md.sha256 | 432ea64531de192671403253a89225650774994c1d625b67f1d393b5c06aa0e4 |
| r296_result_v2.md | 5d9a2826aabfe5e29096afab36c6e2d818e98d5b037bb25dd229171351cb3603 |
| r296_result_v2.md.sha256 | 4ffd2987006f4382973ea3825d639b756300ac6f3582ac92d42c1d1d6e7a952a |
| r296_result_v3.md | d223c05c87b0cb48c7679c45f1727a8f920198685c5d47535e78501ab46fd99a |
| r296_result_v3.md.sha256 | b2e37f08e9e01b981fadfdd0c5a2f0e0fe3eefee932b82b1a761ab2d120f0e8f |
| register_r296_panel_once.py | 180dd3b5c949bb2b72844c6235facce09e2b85ecf5a001278d61d97612f0d54f |

机器索引 JSON SHA-256：4bdc3b14c4d93ad766329a1b7cc3f81dbecfe34c7e30d883fbb10e410b83f9a7。JSON 和 Markdown 索引都有旁置 SHA-256。

结果边界：历史快照 eligible universe 覆盖 249/249；下一日官方名单 eligible universe 覆盖 249/250，缺少新加入的 002294.SZ。仅证明单日隔离构建和系统装配；不证明收益、alpha、历史 PIT 或生产就绪。R257 仍 research-only，生产 pause_production。
