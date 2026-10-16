# 输入说明: 需 backend venv。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r22_sync.py
# 作用: R22 第一步 —— 只同步 HS300 当前 300 只成分的四张财务表。
# 数据写入 <repo>/data/financials/ (运行期数据, 不入 Git)。
# 数据源: preferences 的 financial_data_provider=akshare, 走 custom provider 路径, 不看 TickFlow 套餐门控。
import json
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[6]
DATA_DIR = REPO / "data"

from app.services import financial_sync as fs  # noqa: E402
from app.tickflow.capabilities import CapabilitySet  # noqa: E402

TABLES = ("metrics", "income", "balance_sheet", "cash_flow")


def main() -> int:
    payload = json.loads((DATA_DIR / "hs300" / "current_members.json").read_text(encoding="utf-8"))
    symbols = [m["symbol"] for m in payload["members"]]
    print(
        f"HS300 members: {len(symbols)} (as_of={payload.get('as_of')}, source={payload.get('source')})",
        flush=True,
    )

    capset = CapabilitySet()  # akshare(custom) 分支不读 capset, 传空即可
    failures: list[str] = []
    for table in TABLES:
        started = time.time()
        try:
            rows = fs._sync_history_table_for_symbols(table, symbols, DATA_DIR, capset)
            print(f"[{table}] rows={rows}, {(time.time() - started) / 60:.1f} min", flush=True)
        except Exception:  # noqa: BLE001 - 单表失败不中断其余表
            failures.append(table)
            print(f"[{table}] FAILED after {(time.time() - started) / 60:.1f} min", flush=True)
            traceback.print_exc()
    if failures:
        print(f"failed tables: {failures}", flush=True)
        return 1
    print("all financial tables synced", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
