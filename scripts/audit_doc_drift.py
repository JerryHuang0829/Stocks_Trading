"""Pattern 13 第二類 architectural fix — 自動化 doc drift detection gate.

Adapted from Options_Trading/scripts/audit_doc_drift.py (R11.19 教訓).
Pivot back to Quantitative-Trading 後 (2026-05-02) 接 Pro 工程紀律。

本 script 是 architectural fix 「自動化 grep 全 repo」具體實作，
比 pre-commit hook (要求 git) / GitHub Actions (要求 push remote) 更輕量；
local repo 即可跑.

檢查項目 (4 類 doc drift, 本 repo 累計教訓):

  1. **Stale audit reference** — HANDOFF / CLAUDE / 策略研究 / Claude-Prompt
     中含「下一步：Round <old>」「最後一次 audit: Round <old>」等過時引用
  2. **Stale baseline numbers** — Test baseline single-source-of-truth 之外有
     寫死過時數字 (196/219/224/288/302/342) — current 422
  3. **Stale phase reference** — 已停用因子 / 已淘汰 profile / 已棄用 commands
     被 active reference (institutional_flow weight>0 / quality weight>0 / D1_v3a
     不該再啟用)
  4. **Absolute claim 紅旗** — finding / claim 含「永遠/絕不/必定/不可能/0%」
     絕對句未附反例 stress-test

Exit code: 0 = no drift, 1 = drift found (使 CI / pre-commit / manual run 都
能 catch).

CLI:
  python scripts/audit_doc_drift.py
  python scripts/audit_doc_drift.py --strict  # 把 absolute claim 警告升級為 fail
  python scripts/audit_doc_drift.py --json    # machine-readable output
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Options R12.5 P0 sub-rule (e) — Windows cp950 stdout encoding crash protection.
# 本 script print 中文 / box-drawing char，cp950 default encoding 會 raise
# UnicodeEncodeError. Wrap stdout/stderr with UTF-8 + errors='replace' 避免 crash.
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "").lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Exclude tmp dirs / cache / git / cache parquets
_EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".codex_tmp",
    ".pytest_cache",
    ".pytest_tmp",
    ".venv",
    "venv",
    "env",
    ".vscode",
    ".idea",
    ".ipynb_checkpoints",
    "data",  # cache pickles, not doc
    "logs",
    "CTSwithPython",  # 第三方 SDK 範例，非本 repo doc
}
_EXCLUDE_GLOB_PATTERNS = ("tmp*",)
_EXCLUDE_FILES = {
    "audit_doc_drift.py",  # self-exclude: script 含 keyword 字面是 self-ref 非 drift
    "settings.example.yaml",  # 範例檔展示舊配置，不是 active drift
}
_INCLUDE_EXTS = {".py", ".md", ".json", ".yaml", ".yml", ".toml"}

# Latest known audit round.
# 2026-04-23 R18 Phase A3.1 → 2026-05-02 Pivot back
# → R19 Codex audit on D.0 architecture audit + 3 件 Pro 補強
# → R20 Codex audit on B0 plan v1 (NO-GO; rewrote v2 B0-Lite)
# → R21 Codex audit on B0-Lite spike + pivot P5 decision (GO-WITH-CAVEATS;
#   F1-F4 修法).
# → R22 Codex audit on Plan v4 (NO-GO; 6 blockers — TE conflict / IC source
#   drift / no cross-freq infra / cost units / 2019-2024 over-used / L2-L6 squeeze).
# → R23 Codex audit on Plan v5 partial — confirmed v4 blockers persisted.
# → R24 Codex audit on Plan v5 final (NO-GO; 5 P0 + 7 design issues; v6 rewrite
#   2026-05-04 Phase 0 V0.6 bump). Pattern 18(c) discipline.
LATEST_AUDIT_ROUND = "R24"


@dataclass
class DriftHit:
    file: str
    line: int
    text: str
    category: str

    def fmt(self) -> str:
        return f"  {self.file}:{self.line}  [{self.category}]  {self.text.strip()[:120]}"


@dataclass
class AuditReport:
    stale_audit_refs: list[DriftHit] = field(default_factory=list)
    stale_baselines: list[DriftHit] = field(default_factory=list)
    stale_phase_refs: list[DriftHit] = field(default_factory=list)
    hypothesis_drift: list[DriftHit] = field(default_factory=list)
    absolute_claims: list[DriftHit] = field(default_factory=list)

    @property
    def n_drift(self) -> int:
        return (
            len(self.stale_audit_refs)
            + len(self.stale_baselines)
            + len(self.stale_phase_refs)
            + len(self.hypothesis_drift)
        )

    @property
    def n_warnings(self) -> int:
        return len(self.absolute_claims)


def _walk_repo() -> list[Path]:
    """Yield all repo files under _INCLUDE_EXTS, skipping _EXCLUDE_DIRS."""
    files: list[Path] = []
    for p in _REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _INCLUDE_EXTS:
            continue
        rel = p.relative_to(_REPO_ROOT)
        parts = set(rel.parts)
        if parts & _EXCLUDE_DIRS:
            continue
        if any(rel.parts[0].startswith(g.rstrip("*")) for g in _EXCLUDE_GLOB_PATTERNS):
            continue
        if p.name in _EXCLUDE_FILES:
            continue
        files.append(p)
    return files


def _safe_read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


# 支援 Markdown bold + 中英文冒號 + 「Round N 待 audit」格式
_STALE_AUDIT_REGEX = re.compile(
    r"(?:\*\*)?(?:下一步|最後一次(?:\s+Codex)?\s*audit)(?:\*\*)?\s*[:：]\s*Round\s*\d+"
    r"|Round\s*\d+\s*待\s*(?:Codex|audit)"
    r"|(?:\*\*)?(?:下一步|最後一次(?:\s+Codex)?\s*audit)(?:\*\*)?\s*[:：]\s*R\d+(?:\.\d+)?"
)


def _check_stale_audit_refs(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 18(d): HANDOFF / CLAUDE / Claude-Prompt 中過時 audit round 引用."""
    hits: list[DriftHit] = []
    target_files = {"HANDOFF.md", "CLAUDE.md", "Claude-Prompt.md", "Codex-Prompt.md", "策略研究.md"}
    if p.name not in target_files:
        return hits
    latest_num_match = re.search(r"R(\d+)", LATEST_AUDIT_ROUND)
    latest_num = int(latest_num_match.group(1)) if latest_num_match else None
    # Self-documentation 豁免：行若提及 audit script 自己的 category 名是
    # documentation 範例而非 active drift (e.g. Codex-Prompt 用「下一步：R20」
    # 之類字串作 documentation).
    self_doc_markers = ("stale audit ref", "stale_audit_ref", "之類", "舉例", "範例")
    for i, line in enumerate(lines, 1):
        match = _STALE_AUDIT_REGEX.search(line)
        if not match:
            continue
        if LATEST_AUDIT_ROUND in line:
            continue
        if any(m in line for m in self_doc_markers):
            continue
        # 抽出 Round / R 編號
        round_match = re.search(r"R(?:ound\s*)?(\d+)", match.group(0))
        if round_match is None:
            continue
        round_num = int(round_match.group(1))
        # 若引用編號 == LATEST 不算 stale (regex 上面已過濾，但保險)
        if latest_num is not None and round_num == latest_num:
            continue
        hits.append(
            DriftHit(
                file=str(p.relative_to(_REPO_ROOT)),
                line=i,
                text=line,
                category="stale_audit_ref",
            )
        )
    return hits


def _check_stale_baselines(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 4: stale baseline 數字 (除 single source of truth 那行)."""
    hits: list[DriftHit] = []
    if p.name not in ("HANDOFF.md", "CLAUDE.md", "Claude-Prompt.md", "Codex-Prompt.md"):
        return hits
    # 本 repo 歷史 test counts (current = 451 after B0-Lite Session 1).
    # R19 Codex audit (2026-05-02) 抓到 422/433 missing → 加上.
    # R21 Codex audit (2026-05-03) 抓到 440 missing → 加上 (B0-Lite +11 → 451).
    stale_nums = (
        "196 passed",
        "219 passed",
        "224 passed",
        "288 passed",
        "302 passed",
        "342 passed",
        "421 passed",
        "422 passed",  # R19 Codex audit 加入
        "433 passed",  # R19 Item 2 完成後的中間 baseline
        "440 passed",  # R19 完成 baseline; R21 後 stale (current=451)
        "451 passed",  # R21 B0-Lite Session 1 baseline; V0.2 後 stale (current=462 @ R24)
    )
    # Self-documentation 豁免關鍵字 — 行若提及 audit script 自己的 stale category
    # 名 (如 Codex-Prompt.md 在說明 stale_baselines tuple 內容) 或明示為
    # documentation 範例 (「之前寫」/「舉例」/「範例」) 或為歷史 baseline 對照
    # (含「@ R<round>」歷史時間戳記如 "@ R19") 視為 documentation 而非
    # active baseline claim.
    self_doc_markers = (
        "stale_baselines",
        "stale_nums",
        "stale baseline numbers",
        "stale baseline reference",
        "之前寫",
        "之前寫的",
        "歷史 baseline",
        "舉例",
        "範例",
        "@ R18",
        "@ R19",
        "@ R20",
        "@ R21",
        "@ R22",
        "@ R23",
        "@ R24",
        "B0-Lite Session",  # CLAUDE.md / HANDOFF.md 用 "@ B0-Lite Session 1 後" 標歷史 baseline
    )
    for i, line in enumerate(lines, 1):
        # 「成長歷程」「歷程」段落豁免 (寫的是「196 → 219 → ...」歷史軌跡)
        if "成長歷程" in line or "歷程" in line or "→" in line:
            continue
        if any(m in line for m in self_doc_markers):
            continue
        for num in stale_nums:
            if num in line:
                hits.append(
                    DriftHit(
                        file=str(p.relative_to(_REPO_ROOT)),
                        line=i,
                        text=line,
                        category="stale_baseline",
                    )
                )
                break
    return hits


def _check_stale_phase_refs(p: Path, lines: list[str]) -> list[DriftHit]:
    """已淘汰 phase / 已棄用 profile / 已停用因子 active reference.

    本 repo 累計教訓:
    - institutional_flow / quality 已停用 (weight=0)；config / yaml 中 weight>0 = drift
    - D1_v3a / D1_v3b 已 strict gate fail；HANDOFF/CLAUDE 中標 「下一階段使用」 = drift
    - tw_3m_stable 是 ACTIVE profile 不算 drift
    """
    hits: list[DriftHit] = []
    if p.suffix not in (".yaml", ".yml", ".md"):
        return hits
    target_files_md = {"HANDOFF.md", "CLAUDE.md", "Claude-Prompt.md", "策略研究.md"}
    is_md = p.suffix == ".md"
    is_yaml = p.suffix in (".yaml", ".yml")
    if is_md and p.name not in target_files_md:
        return hits

    for i, line in enumerate(lines, 1):
        # YAML active weight check (institutional_flow / quality > 0)
        if is_yaml:
            yaml_match = re.match(
                r"\s*(institutional_flow|quality)\s*:\s*0?\.\d*[1-9]+",
                line,
            )
            if yaml_match:
                hits.append(
                    DriftHit(
                        file=str(p.relative_to(_REPO_ROOT)),
                        line=i,
                        text=line,
                        category="stale_phase_ref_yaml",
                    )
                )
        # MD active reference to deprecated configs
        if is_md:
            # 「啟用 D1_v3a」「使用 D1_v3b」等正向動詞 active reference
            if re.search(r"(啟用|使用|採用|改用)\s*D1_v3[ab]?", line):
                # 「不要再啟用」「不重做」豁免
                if any(neg in line for neg in ("不要", "不重做", "不啟動", "不啟用", "不再", "已停")):
                    continue
                hits.append(
                    DriftHit(
                        file=str(p.relative_to(_REPO_ROOT)),
                        line=i,
                        text=line,
                        category="stale_phase_ref_md",
                    )
                )
    return hits


def _check_hypothesis_drift(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 13 第二類 architectural fix — hypothesis pre-registration drift detector.

    R21 Codex audit (2026-05-03) P1 fix：
    H_lite_preregistration.md (line 6) 寫「由 audit_doc_drift.py 的 hypothesis-drift
    detector 監看」但實作沒有 → 承諾與實作不一致.

    R24 Codex audit (2026-05-04) P0-3 fix：擴 phase_d/ 覆蓋（v6 H_d_v6_preregistration.md
    + R24_resolution.md 進入 hypothesis lock 紀律）。

    監看對象：reports/phase_b0_lite/, reports/phase_b1/, reports/phase_b2/, ...,
    reports/phase_d/, reports/phase_p5/（任何 phase_b*_lite/ 或 phase_b*/ 或
    phase_d/ 或 phase_p*/ 含 H_*_preregistration.md 的目錄）。

    觸發 fail 的 patterns：
    - 「修改 H_lite」「修改 H_main」「修改 H_p5」「修改 H_<名>」等動詞
    - 「rebid H_lite」「rebid H_main」等動詞
    - 「retroactive H_」 / 「事後改 H_」/「事後修改 hypothesis」
    - 「relax reject criteria」「降標 reject」「reject criteria 改寬鬆」
    - 「DSR threshold 從 0.95 改 0.90」/ 「IC threshold 從 0.02 改」（threshold 任何 retroactive 動詞）

    豁免：Codex-Prompt.md 引用本 detector 的 docstring（「修改 H_lite 改 reject criteria
    改 0.018 為 acceptable」當 example 字面）。
    """
    hits: list[DriftHit] = []
    # 只檢 phase_b* 目錄下的 hypothesis pre-registration files + decision files
    rel = p.relative_to(_REPO_ROOT)
    parts = rel.parts
    if not (
        len(parts) >= 2
        and parts[0] == "reports"
        and any(parts[1].startswith(prefix) for prefix in ("phase_b0", "phase_b1", "phase_b2", "phase_b3", "phase_b4", "phase_b5", "phase_d", "phase_p5", "phase_p"))
    ):
        return hits

    # Hypothesis-drift verbs (any combo with H_<name> identifier triggers)
    drift_verbs = (
        "修改 H_",
        "rebid H_",
        "retroactive H_",
        "事後改 H_",
        "事後修改 hypothesis",
        "事後改 hypothesis",
        "relax reject criteria",
        "降標 reject",
        "reject criteria 改寬鬆",
        "DSR threshold 從",
        "IC threshold 從",
        "threshold 從 0.95 改",
        "threshold 從 0.02 改",
    )
    # Self-doc 豁免：Codex prompt 文字引用 detector docstring 內 example 字面
    self_doc_markers = (
        "hypothesis-drift detector",
        "hypothesis_drift detector",
        "detector 範例",
        "detector example",
        "監看",  # 文件介紹 detector 功能用詞
        "字面 → audit fail",  # H_lite line 6 自我描述
        "改寬鬆 reject criteria 監看",  # 自我描述 detector
    )

    for i, line in enumerate(lines, 1):
        if any(verb in line for verb in drift_verbs):
            if any(m in line for m in self_doc_markers):
                continue
            hits.append(
                DriftHit(
                    file=str(rel),
                    line=i,
                    text=line,
                    category="hypothesis_drift",
                )
            )
    return hits


def _check_absolute_claims(p: Path, lines: list[str]) -> list[DriftHit]:
    """Pattern 18(a): 絕對句紅旗 (warn 不 fail by default)."""
    hits: list[DriftHit] = []
    if p.suffix != ".md":
        return hits
    abs_keys = ("永遠不", "絕不", "必定不會", "永遠都", "不可能")
    safety_markers = ("但 ", "除非", "except", "反例", "caveat", "條件", "stress-test")
    for i, line in enumerate(lines, 1):
        if any(k in line for k in abs_keys) and not any(m in line for m in safety_markers):
            hits.append(
                DriftHit(
                    file=str(p.relative_to(_REPO_ROOT)),
                    line=i,
                    text=line,
                    category="absolute_claim",
                )
            )
    return hits


def run_audit() -> AuditReport:
    """Run all 5 doc drift checks across repo. Return aggregated report."""
    report = AuditReport()
    files = _walk_repo()
    for p in files:
        lines = _safe_read_lines(p)
        report.stale_audit_refs.extend(_check_stale_audit_refs(p, lines))
        report.stale_baselines.extend(_check_stale_baselines(p, lines))
        report.stale_phase_refs.extend(_check_stale_phase_refs(p, lines))
        report.hypothesis_drift.extend(_check_hypothesis_drift(p, lines))
        report.absolute_claims.extend(_check_absolute_claims(p, lines))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pattern 13 第二類 doc drift gate")
    parser.add_argument("--strict", action="store_true", help="absolute claims 警告升級為 fail")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    report = run_audit()

    if args.json:
        out = {
            "n_drift": report.n_drift,
            "n_warnings": report.n_warnings,
            "stale_audit_refs": [vars(h) for h in report.stale_audit_refs],
            "stale_baselines": [vars(h) for h in report.stale_baselines],
            "stale_phase_refs": [vars(h) for h in report.stale_phase_refs],
            "hypothesis_drift": [vars(h) for h in report.hypothesis_drift],
            "absolute_claims": [vars(h) for h in report.absolute_claims],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("=" * 70)
        print(f"Doc drift audit — Quantitative-Trading (LATEST_AUDIT_ROUND={LATEST_AUDIT_ROUND})")
        print("=" * 70)
        for label, hits in [
            ("Stale audit refs (Pattern 18 d)", report.stale_audit_refs),
            ("Stale baseline numbers (Pattern 4)", report.stale_baselines),
            ("Stale phase / deprecated config refs", report.stale_phase_refs),
            ("Hypothesis pre-registration drift (R21 P1)", report.hypothesis_drift),
        ]:
            print(f"\n[{label}] hits: {len(hits)}")
            for h in hits:
                print(h.fmt())
        print(f"\n[Absolute claims (Pattern 18 a) — warning] hits: {len(report.absolute_claims)}")
        for h in report.absolute_claims:
            print(h.fmt())
        print()
        print(f"Total drift: {report.n_drift}; Warnings: {report.n_warnings}")

    fail = report.n_drift > 0 or (args.strict and report.n_warnings > 0)
    if fail:
        print("\nFAIL: doc drift detected.", file=sys.stderr)
        return 1
    print("\nPASS: no doc drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
