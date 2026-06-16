"""Evaluation runner — generates HTML report.
Usage: python -m evaluation.runner [--output-report path/to/report.html]
"""

import argparse
from datetime import datetime
from evaluation.metrics import TestResult, compute_all_metrics, check_targets, TARGETS
from evaluation.test_scenarios import TEST_CASES


def _badge(passed: bool) -> str:
    color = "#2ecc71" if passed else "#e74c3c"
    label = "PASS" if passed else "FAIL"
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{label}</span>'


def _format_value(metric: str, value: float) -> str:
    if metric == "latency_p95_ms":
        return f"{value:.0f} ms"
    return f"{value*100:.1f}%"


def generate_html_report(results: list[TestResult], metrics: dict[str, float], passed: dict[str, bool]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    all_pass = all(passed.values())
    overall_color = "#2ecc71" if all_pass else "#e74c3c"
    overall_label = "ALL TARGETS MET" if all_pass else "TARGETS NOT MET"

    metrics_rows = ""
    for metric, value in metrics.items():
        op, target = TARGETS[metric]
        target_str = f"{op} {target*100:.0f}%" if metric != "latency_p95_ms" else f"{op} {target:.0f} ms"
        metrics_rows += f"<tr><td>{metric.replace('_', ' ').title()}</td><td>{_format_value(metric, value)}</td><td>{target_str}</td><td>{_badge(passed[metric])}</td></tr>"

    by_category: dict[str, list[TestResult]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r)

    category_sections = ""
    for cat, cat_results in by_category.items():
        rows = "".join(
            f"<tr><td>{r.test_id}</td><td>{r.expected_intent}</td><td>{r.expected_route.replace('_', ' ')}</td>"
            f"<td>{r.expected_agent or '—'}</td><td>{'Yes' if r.expected_blocked else 'No'}</td>"
            f"<td>{'Yes' if r.expected_hitl else 'No'}</td><td style=\"color:{'green' if not r.error else 'red'}\">{'✓' if not r.error else '✗'}</td></tr>"
            for r in cat_results
        )
        category_sections += f"<h3>{cat.title()} Cases ({len(cat_results)})</h3><table><tr><th>ID</th><th>Intent</th><th>Route</th><th>Agent</th><th>Blocked</th><th>HITL</th><th>Status</th></tr>{rows}</table>"

    return f"""<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\"><title>Insurance Bot Evaluation</title>
<style>body{{font-family:Arial,sans-serif;max-width:1100px;margin:40px auto;color:#333}}
h1{{color:#2c3e50}}h2{{color:#34495e;border-bottom:2px solid #ecf0f1;padding-bottom:6px}}
table{{border-collapse:collapse;width:100%;margin-bottom:24px}}
th{{background:#2c3e50;color:white;padding:8px 12px;text-align:left}}
td{{padding:7px 12px;border-bottom:1px solid #ecf0f1}}
tr:nth-child(even){{background:#f9f9f9}}
.badge{{background:{overall_color};color:white;padding:6px 16px;border-radius:6px;font-size:16px;font-weight:bold;display:inline-block;margin:12px 0}}</style></head>
<body><h1>&#128737; Insurance Bot — Evaluation Report</h1>
<p><strong>Generated:</strong> {ts}</p>
<div class=\"badge\">{overall_label}</div>
<h2>Summary Metrics</h2><table><tr><th>Metric</th><th>Result</th><th>Target</th><th>Status</th></tr>{metrics_rows}</table>
<h2>Test Cases ({len(results)} total)</h2>{category_sections}
<hr><p style=\"color:#888;font-size:12px\">ADK 2.2.0 Insurance Bot | Phase 1 | {ts}</p>
</body></html>"""


def run_evaluation(output_report: str = "evaluation/reports/report.html") -> None:
    import os
    os.makedirs(os.path.dirname(output_report), exist_ok=True)
    results = TEST_CASES
    metrics = compute_all_metrics(results)
    passed = check_targets(metrics)
    print("\nEvaluation Results:")
    print("-" * 50)
    for metric, value in metrics.items():
        op, target = TARGETS[metric]
        status = "✓" if passed[metric] else "✗"
        print(f"  {status} {metric}: {_format_value(metric, value)} (target {op} {target})")
    html = generate_html_report(results, metrics, passed)
    with open(output_report, "w") as f:
        f.write(html)
    print(f"\nReport written to: {output_report}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-report", default="evaluation/reports/report.html")
    args = parser.parse_args()
    run_evaluation(args.output_report)
