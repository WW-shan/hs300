#!/usr/bin/env python3
"""Run the R297 audit with a narrowly documented mutable-cache exception."""
from __future__ import annotations
import hashlib, importlib.util, json, sys
from datetime import UTC, datetime
from pathlib import Path
EVIDENCE=Path(__file__).resolve().parent
ROOT=Path(__file__).resolve().parents[6]
SOURCE=EVIDENCE/"audit_r297_counterfactual_membership.py"
PRE=EVIDENCE/"r297_preregistration.json"
MANIFEST=EVIDENCE/"r297_input_manifest.jsonl"
BASELINE=EVIDENCE/"r297_formal_factor_baseline.json"
SCOPE=EVIDENCE/"r297_scope_correction.json"
CACHE=ROOT/"data/hs300/current_members.json"
OFFICIAL=EVIDENCE/"r297_official_current_members.json"
RESULT=EVIDENCE/"r297_counterfactual_membership_audit.json"
REPORT=EVIDENCE/"r297_result.md"
def sha_bytes(value:bytes)->str: return hashlib.sha256(value).hexdigest()
def sha_file(path:Path)->str:
 h=hashlib.sha256()
 with path.open("rb") as f:
  for block in iter(lambda:f.read(1<<20),b""): h.update(block)
 return h.hexdigest()
def member_hash(path:Path)->str:
 d=json.loads(path.read_text(encoding="utf-8")); rows=sorted((x["symbol"],x.get("name","")) for x in d["members"])
 return sha_bytes(json.dumps(rows,ensure_ascii=False,separators=(",",":")).encode("utf-8"))
def verify()->dict:
 pre=json.loads(PRE.read_text(encoding="utf-8")); ph=sha_file(PRE)
 if ph!=PRE.with_name(PRE.name+".sha256").read_text(encoding="ascii").split()[0] or pre.get("round")!="R297" or pre.get("status")!="frozen_before_compute": raise RuntimeError("R297 preregistration identity/hash mismatch")
 raw=MANIFEST.read_bytes(); rows=[json.loads(x) for x in raw.decode().splitlines() if x]; mh=sha_bytes(raw)
 if mh!=pre["frozen_inputs"]["manifest_sha256"]: raise RuntimeError("R297 manifest hash mismatch")
 scope=json.loads(SCOPE.read_text(encoding="utf-8")); ce=next(x for x in rows if x["path"]=="data/hs300/current_members.json")
 if ce["sha256"]!=scope["observed_cache_before_compute"]["frozen_sha256"]: raise RuntimeError("Scope correction does not identify frozen cache")
 if member_hash(OFFICIAL)!=scope["observed_cache_before_compute"]["frozen_semantic_members_sha256"]: raise RuntimeError("Frozen official member identity mismatch")
 if member_hash(CACHE)!=member_hash(OFFICIAL): raise RuntimeError("Live cache member set diverges from frozen official snapshot")
 checked=0
 for row in rows:
  if row["path"]=="data/hs300/current_members.json": continue
  p=ROOT/row["path"]; s=p.stat()
  if (s.st_size,s.st_mtime_ns,sha_file(p))!=(row["bytes"],row["mtime_ns"],row["sha256"]): raise RuntimeError(f"Frozen input changed: {row['path']}")
  checked+=1
 rawb=BASELINE.read_bytes(); bh=sha_bytes(rawb); exp=BASELINE.with_name(BASELINE.name+".sha256").read_text(encoding="ascii").split()[0]
 if bh!=exp: raise RuntimeError("Formal factor baseline hash mismatch")
 baseline=json.loads(rawb); parts=[x for tree in baseline["factor_trees"] for x in tree["partitions"]]
 for row in parts:
  p=ROOT/row["path"]; s=p.stat()
  if (s.st_size,s.st_mtime_ns,sha_file(p))!=(row["size_bytes"],row["mtime_ns"],row["sha256"]): raise RuntimeError(f"Formal factor partition changed: {row['path']}")
 return {"preregistration_sha256":ph,"manifest_sha256":mh,"baseline_sha256":bh,"immutable_source_files_verified":checked,"formal_factor_partitions_verified":len(parts),"mutable_cache_raw_hash_excluded":True,"mutable_cache_semantic_members_equal":True}
def main()->int:
 if RESULT.exists() or REPORT.exists(): raise RuntimeError("R297 output exists; refusing overwrite")
 before=verify()
 spec=importlib.util.spec_from_file_location("r297_audit_original",SOURCE)
 if spec is None or spec.loader is None: raise RuntimeError("Cannot load separately frozen audit")
 module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
 original=module.verify_manifest
 def scoped(prereg):
  raw=MANIFEST.read_bytes()
  if sha_bytes(raw)!=prereg["frozen_inputs"]["manifest_sha256"]: raise RuntimeError("R297 input manifest changed")
  rows=[json.loads(line) for line in raw.decode("utf-8").splitlines() if line]; out=[]
  for row in rows:
   if row["path"]=="data/hs300/current_members.json":
    if member_hash(CACHE)!=member_hash(OFFICIAL): raise RuntimeError("Runtime cache differs semantically from frozen official membership")
    continue
   p=ROOT/row["path"]; st=p.stat()
   if (st.st_size,st.st_mtime_ns,sha_file(p))!=(row["bytes"],row["mtime_ns"],row["sha256"]): raise RuntimeError(f"Frozen input changed: {row['path']}")
   out.append(row)
  return out
 module.verify_manifest=scoped
 try: module.main()
 finally: module.verify_manifest=original
 after=verify(); machine=json.loads(RESULT.read_text(encoding="utf-8")); machine["scope_correction_sha256"]=sha_file(SCOPE)
 machine["execution_limits"].update({"precompute_scope_correction_recorded":True,"runtime_cache_raw_hash_frozen":False,"runtime_cache_semantic_members_equal_before_and_after":True,"immutable_source_files_verified_before_and_after":after["immutable_source_files_verified"],"formal_factor_partitions_verified_before_and_after":after["formal_factor_partitions_verified"],"scope_check_before":before,"scope_check_after":after})
 machine["captured_at_utc"]=datetime.now(UTC).isoformat(); payload=(json.dumps(machine,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode(); RESULT.write_bytes(payload); msha=sha_bytes(payload); RESULT.with_name(RESULT.name+".sha256").write_text(f"{msha}  {RESULT.name}\n",encoding="ascii")
 text=REPORT.read_text(encoding="utf-8")
 note=("- 范围更正 SHA-256："+sha_file(SCOPE)+"。计算前发现面板服务刷新了 TTL 缓存；仅豁免该缓存的原始字节哈希，仍核对它与独立冻结官方名单的成分和名称完全一致。原冻结预注册和 manifest 均未修改。\n")
 text=text.replace("## 保护与限制\n","## 保护与限制\n\n"+note).replace("冻结输入 ","原冻结清单除上述 TTL 缓存外的输入 ")
 REPORT.write_text(text,encoding="utf-8"); rsha=sha_file(REPORT); REPORT.with_name(REPORT.name+".sha256").write_text(f"{rsha}  {REPORT.name}\n",encoding="ascii")
 print(json.dumps({"round":"R297","status":machine["status"],"before":before,"after":after,"machine_result_sha256":msha,"report_sha256":rsha},ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())

