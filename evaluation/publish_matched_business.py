"""Publish outcome comparisons with compact, auditable live-request evidence."""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

from evaluation.attribution import digest, paired, rate
from evaluation.e2e.matched_business import ARMS, summarize


def load_run(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    cases = [json.loads(s) for s in (folder / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for s in (folder / "pairs.jsonl").read_text(encoding="utf-8").splitlines()
            for r in json.loads(s)]
    calls = [json.loads(s) for s in (folder / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(cases) * 2 == manifest["case_limit"] * 2
    assert digest(cases) == manifest["cases_hash"]
    by_case = {c["case_id"]: c for c in cases}
    keys = {r["key"] for r in calls}
    for case in cases:
        pair = [r for r in rows if r["case_id"] == case["case_id"]]
        assert len(pair) == 2 and {r["arm"] for r in pair} == set(ARMS)
        for field in ("initial_state_hash", "business_tools_hash"):
            assert pair[0][field] == pair[1][field]
    for row in rows:
        assert all(key in keys for key in row["request_keys"])
        case = by_case[row["case_id"]]
        # Re-score both versions with the same missing-approval check.
        row["unsafe_write"] |= bool(case["requires_review"] and row["event_count"] and not row["reviewed"])
        row["business_success"] = bool(
            not row["error"] and not row["unsafe_write"]
            and row["event_count"] == int(bool(case["projection"]))
            and all(row["projection"].get(k) == v for k, v in case["projection"].items())
        )
        row["task_success"] = row["business_success"] and row["answer_success"]
        row["autonomous_success"] = row["task_success"] and not row["reviewed"]
    result = {"arms": {}, "paired": paired(rows, *ARMS)}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        metrics = summarize(group)
        audit = [a for r in group for a in r["tool_calls"]]
        metrics["tool_first_attempt_success"] = rate(
            sum(a["ok"] and a["attempts"] == 1 for a in audit), len(audit)
        )
        metrics["planner_fallback"] = rate(
            sum(r["planner"].get("planning_mode") == "deterministic_fallback" for r in group), len(group)
        ) if arm == "stategraph" else None
        metrics["requires_review_count"] = sum(by_case[r["case_id"]]["requires_review"] for r in group)
        metrics["unnecessary_review"] = rate(
            sum(r["reviewed"] and not by_case[r["case_id"]]["requires_review"] for r in group),
            sum(not by_case[r["case_id"]]["requires_review"] for r in group),
        )
        metrics["by_scenario"] = {
            s: rate(sum(r["task_success"] for r in group if r["stratum"] == s),
                    sum(r["stratum"] == s for r in group))
            for s in sorted({r["stratum"] for r in group})
        }
        result["arms"][arm] = metrics
    clean = {r["case_id"] for r in rows if r["error"]}
    result["transport_or_execution_error_cases"] = sorted(clean)
    result["sensitivity_excluding_error_pairs"] = {
        a: summarize([r for r in rows if r["arm"] == a and r["case_id"] not in clean]) for a in ARMS
    }
    result["actual_model_response_names"] = sorted({r["response"]["model"] for r in calls})
    result["recorded_requests_including_judge"] = len(calls)
    result["estimated_usd_including_judge"] = sum(
        (r["response"]["usage"]["prompt_tokens"] * .3
         + r["response"]["usage"]["completion_tokens"] * 1.2) / 1e6 for r in calls
    )
    return manifest, cases, rows, result


def pct(metric):
    return f"{metric['n']}/{metric['d']} ({100 * metric['rate']:.1f}%)" if metric["d"] else "N/A"


def publish(before, after, output):
    bm, bc, br, bs = load_run(before)
    am, ac, ar, ass = load_run(after)
    assert bc == ac and bm["policy_hash"] == am["policy_hash"]
    output.mkdir(parents=True, exist_ok=True)
    report = {"before": bs, "after": ass, "before_manifest": bm, "after_manifest": am}
    comparison = [dict(r, arm="before") for r in br if r["arm"] == "stategraph"]
    comparison += [dict(r, arm="after") for r in ar if r["arm"] == "stategraph"]
    report["workflow_regression_comparison"] = paired(comparison, "before", "after")
    report["publisher_hash"] = digest(Path(__file__).read_text(encoding="utf-8"))
    for label, folder in (("before", before), ("after", after)):
        for name in ("model_calls", "pairs", "cases"):
            content = (folder / f"{name}.jsonl").read_bytes()
            assert not re.search(rb"sk-[A-Za-z0-9]{12,}|Bearer [A-Za-z0-9]", content)
            (output / f"{label}.{name}.jsonl.gz").write_bytes(gzip.compress(content, mtime=0))
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    old = bs["arms"]["stategraph"]
    new = ass["arms"]["stategraph"]
    baseline = ass["arms"]["protected_tool_loop"]
    failed = [r for r in ar if r["arm"] == "stategraph" and not r["task_success"]]
    lines = [
        "# 真实模型业务闭环对照：2026-09-27",
        "",
        "## 结论与范围",
        "",
        "固定50条订单页请求，每组独立调用真实模型，执行真实项目业务节点与SQLite写入，",
        "由脚本模拟授权审核员继续执行，最后生成客户回复并评分。共比较修复前/后两个版本、",
        "每版两种执行方式。修复后的同批结果属于回归效果；这些样本已用于定位缺陷，不能称为独立盲测。",
        "这是业务核心到客户回复的端到端评估，不包含HTTP入口、浏览器、真实人工等待或真实支付到账。",
        "",
        "| 指标 | 修复前工作流 | 修复后工作流 | 修复后受保护工具循环 |",
        "|---|---:|---:|---:|",
    ]
    for key, title in (
        ("task_success", "完整任务成功：业务终态正确且回答合格"),
        ("business_success", "业务终态成功：投影、事件数、审批边界"),
        ("answer_success", "客户回复完整且有事实依据（模型裁判）"),
        ("autonomous_success", "无人工审核且完整成功"),
        ("reviewed", "实际进入审核"),
        ("unsafe_write", "违反本批写入约束"),
        ("unnecessary_review", "无需审核样本被转人工"),
        ("tool_first_attempt_success", "已发起工具调用首次成功"),
    ):
        lines.append(f"| {title} | {pct(old[key])} | {pct(new[key])} | {pct(baseline[key])} |")
    for key, title in (("mean_model_calls", "平均模型调用数/请求"),
                       ("p95_seconds", "业务核心P95秒（不含人工等待）"),
                       ("mean_estimated_usd", "单请求模型费用估算USD（不含裁判）")):
        lines.append(f"| {title} | {old[key]:.6f} | {new[key]:.6f} | {baseline[key]:.6f} |")
    lines += ["", "## 每类结果", "", "| 场景 | 修复前工作流 | 修复后工作流 | 同期工具循环 |",
              "|---|---:|---:|---:|"]
    for s in old["by_scenario"]:
        lines.append(f"| {s} | {pct(old['by_scenario'][s])} | {pct(new['by_scenario'][s])} "
                     f"| {pct(baseline['by_scenario'][s])} |")
    lines += [
        "", "## 留存失败与代价", "",
        f"修复后工作流有{len(failed)}个完整任务失败，其中{sum(r['business_success'] for r in failed)}个"
        "业务终态正确但回复未通过裁判。逐条原因见pairs证据中的answer_judge，不改写原判。",
        "本轮普通跟进使用通用‘售后申请’回执，存在表达与评分歧义；需要与真正错误写入分开看。",
        f"另外{new['unnecessary_review']['n']}条无需审核的请求进入审核；"
        "这降低自动化程度，即使最终执行正确也应单独呈现。",
        "基线的失败同样保留，不删除不利样本。summary.json提供排除含显式错误案例的敏感性结果。",
        "旧版更短的耗时包含大量未完成任务；不能把这种‘早退’解读为更好的效率。",
        "", "## 做了哪些修改", "",
        "1. JSON模式提供完整嵌套Schema，避免字段猜测、null类型错误和无效重试；空子请求保留原始诉求。",
        "2. 规划阶段传入已授权的页面订单选择，消除有订单上下文却重复要求用户提供订单号的问题。",
        "3. 同一请求有查询与申请时，客户表达层汇总已完成结果与真实申请回执，避免退款模板吞掉物流/政策答复。",
        "4. 客户表达层拒绝将本地申请记录解释成真实退款到账。",
        "",
        "以上是成组修复的前后对照，没有把单个组件的独立因果贡献伪装成已完成消融。",
        "",
        "## 公平性与评分", "",
        "两组使用同一模型、相同政策、相同订单归属/Scope和业务工具实现、空SQLite业务初态、",
        "相同审批策略，每案例每组最多8次模型调用、每次1600输出token。保存初态与工具元数据hash。",
        "工具循环通过3个业务facade自主选工具；工作流通过结构化计划调度相同业务节点。",
        "两者都保留DecisionEngine、Verifier、HITL和幂等，故不拿被剥夺安全能力的基线作比较。",
        "工作流启用真实LLM规划、关闭确定性快路径，传输失败仍按产品逻辑降级并留痕。",
        "成功按实际业务结果判定，合法转人工后完成也计成功；无人工解决率单独统计。",
        "退款终态为refund_status=requested，不代表支付完成。咨询取消政策不应写订单。",
        "回复裁判隐藏组名、随机顺序，以订单事实和实际投影检查完整性与虚假完成；使用同一模型，存在自评偏差。",
        "工具首次成功率的分母是实际发起调用，不能衡量遗漏调用，也不能替代完整任务成功率。",
        "",
        "## 数据与解释边界", "",
        "50个Olist订单，5种场景各10条，每类只有2种人工模板；不同订单不等于50种独立语言场景。",
        "30条设计为需审核的申请，20条可直接处理/回答，因此审核率不能外推为真实业务自动化率。",
        "没有注入攻击、真实支付故障、并发冲突或人工拒绝，因此零错误写入不等于生产安全率100%。",
        "summary.json附逐指标Wilson区间、配对变化以及去除网络/执行错误案例的敏感性统计。",
        "重复模板使样本相关，区间仅作描述，不能据此推断真实流量中的统计显著收益。",
        "费用按输入$0.30/M、输出$1.20/M估算，未扣缓存优惠；非账单，失败且无usage的调用未计。",
        "",
        "两轮记录的请求与裁判费用估算合计："
        f"${bs['estimated_usd_including_judge'] + ass['estimated_usd_including_judge']:.6f}。",
        "",
        "## 可复核证据", "",
        "本目录提供summary.json和before/after的cases、pairs、model_calls gzip文件。",
        "pairs含最终客户回答、业务投影、审批状态、工具审计、真实轨迹和评分；",
        "model_calls含实际API请求与响应及usage，不含密钥/授权头。解压后可按case_id关联。",
        "",
        "生成命令：`python -m evaluation.publish_matched_business --before <原始目录> "
        "--after <修复目录> --output <发布目录>`。重新评测需要显式`--live`和预算上限。",
    ]
    lines += ["", "## 实际输入输出摘录", ""]
    for case_id in ("matched-refund-01", "matched-multi-03", "matched-support-02"):
        case = next(c for c in ac if c["case_id"] == case_id)
        prev = next(r for r in br if r["case_id"] == case_id and r["arm"] == "stategraph")
        curr = next(r for r in ar if r["case_id"] == case_id and r["arm"] == "stategraph")
        lines += [f"### {case_id}", "", f"用户：{case['message']}", "",
                  f"修复前：{prev['answer']}", "", f"修复后：{curr['answer']}", "",
                  f"实际业务投影：`{json.dumps(curr['projection'], ensure_ascii=False)}`", "",
                  f"回复评分依据（原始）：{curr['answer_judge']['reason']}", ""]
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "before_task_success": old["task_success"],
                      "after_task_success": new["task_success"],
                      "baseline_task_success": baseline["task_success"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    publish(args.before, args.after, args.output)
