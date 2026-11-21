from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];PREREG=ROOT/'data/factor_research/research/r445_defensive_index_feasibility_preregistration.json'
def test_r445_freezes_defensive_candidates():
 d=json.loads(PREREG.read_text());assert d['round']=='R445';assert d['candidates']['defensive']==['000015.SH','000012.SH'];assert d['output_policy']['active_version'] is None
def test_r445_sidecar():
 p=PREREG;assert hashlib.sha256(p.read_bytes()).hexdigest()==p.with_name(p.name+'.sha256').read_text().split()[0]
