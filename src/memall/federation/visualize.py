import base64
import html
import io
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False

from memall.federation.health import federation_health

OUTPUT_DIR = Path.home() / ".memall" / "reports"


def _ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    buf.close()
    plt.close(fig)
    return data


def _plot_kpi_cards(stats: dict) -> str:
    fig, axes = plt.subplots(1, 4, figsize=(10, 2.5))
    fig.patch.set_facecolor("#f5f5f5")
    cards = [
        ("总记忆数", stats["total"], "#4CAF50"),
        ("Agent 数", len(stats["agents"]), "#2196F3"),
        ("未解决冲突", stats["open_conflicts"], "#FF5722"),
        ("已解决冲突", stats["resolved_conflicts"], "#9E9E9E"),
    ]
    for ax, (label, value, color) in zip(axes, cards):
        ax.set_facecolor(color)
        ax.text(0.5, 0.65, str(value), ha="center", va="center", fontsize=28, fontweight="bold", color="white")
        ax.text(0.5, 0.2, label, ha="center", va="center", fontsize=10, color="white", alpha=0.9)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
    plt.tight_layout(pad=1)
    return _fig_to_base64(fig)


def _plot_agent_distribution(stats: dict) -> str:
    agents = stats.get("agents", {})
    if not agents:
        return ""
    names = list(agents.keys())
    counts = list(agents.values())
    fig, ax = plt.subplots(figsize=(8, max(3, len(names) * 0.4)))
    colors = plt.cm.Blues([0.4 + 0.5 * i / max(1, len(names)) for i in range(len(names))])
    ax.barh(names, counts, color=colors)
    ax.set_xlabel("记忆数")
    ax.set_title("Agent 贡献分布")
    for i, v in enumerate(counts):
        ax.text(v + 0.3, i, str(v), va="center", fontsize=9)
    plt.tight_layout()
    return _fig_to_base64(fig)


def _plot_conflict_pie(stats: dict) -> str:
    status = stats.get("conflict_status", {})
    if not status:
        return ""
    labels = {"none": "无冲突", "potential": "潜在冲突", "resolved": "已解决", "superseded": "已覆盖"}
    keys = [k for k in ["none", "potential", "resolved", "superseded"] if k in status and status[k] > 0]
    values = [status[k] for k in keys]
    display_labels = [labels.get(k, k) for k in keys]
    colors = ["#4CAF50", "#FF9800", "#2196F3", "#9E9E9E"][:len(keys)]
    fig, ax = plt.subplots(figsize=(5, 4))
    wedges, texts, autotexts = ax.pie(values, labels=display_labels, autopct="%1.1f%%",
                                       colors=colors, startangle=90, textprops={"fontsize": 9})
    ax.set_title("Conflict Status 分布")
    plt.tight_layout()
    return _fig_to_base64(fig)


def _plot_trend(stats: dict) -> str:
    trend = stats.get("trend", [])
    if len(trend) < 2:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "数据不足（需要至少 2 天快照）", ha="center", va="center", fontsize=12, transform=ax.transAxes)
        ax.axis("off")
        plt.tight_layout()
        return _fig_to_base64(fig)
    dates = [t["date"][5:] for t in trend]
    totals = [t["total"] for t in trend]
    opens = [t.get("open_conflicts", 0) for t in trend]
    fig, ax1 = plt.subplots(figsize=(8, 3.5))
    ax1.plot(dates, totals, "o-", color="#2196F3", linewidth=2, label="总记忆数")
    ax1.set_xlabel("日期")
    ax1.set_ylabel("总记忆数", color="#2196F3")
    ax1.tick_params(axis="y", labelcolor="#2196F3")
    ax2 = ax1.twinx()
    ax2.plot(dates, opens, "s--", color="#FF5722", linewidth=2, label="未解决冲突")
    ax2.set_ylabel("未解决冲突", color="#FF5722")
    ax2.tick_params(axis="y", labelcolor="#FF5722")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    ax1.set_title("记忆增长趋势")
    plt.tight_layout()
    return _fig_to_base64(fig)


def _build_html(kpi_img: str, agent_img: str, pie_img: str, trend_img: str,
                stats: dict, detail: bool = False) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    detail_section = ""
    if detail:
        dups = stats.get("duplicates", [])
        orphans = stats.get("orphans", [])
        if orphans:
            detail_section += "<h3>孤岛记忆</h3><table><tr><th>ID</th><th>内容</th></tr>"
            for o in orphans[:10]:
                detail_section += f"<tr><td>#{o['id']}</td><td>{html.escape(str(o['content']))}</td></tr>"
            detail_section += "</table>"
        if dups:
            detail_section += "<h3>近似重复</h3><table><tr><th>ID</th><th>内容</th><th>相似</th><th>相似 ID</th></tr>"
            for d in dups[:10]:
                detail_section += f"<tr><td>#{d['id']}</td><td>{html.escape(str(d['content']))}</td><td>{d['similarity']}</td><td>#{d['most_similar_id']}</td></tr>"
            detail_section += "</table>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>MemALL Federation Health Report — {now[:10]}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
h1 {{ color: #333; border-bottom: 2px solid #2196F3; padding-bottom: 8px; }}
h2 {{ color: #555; margin-top: 24px; }}
img {{ max-width: 100%; margin: 8px 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; font-size: 13px; }}
th {{ background: #2196F3; color: white; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.kpi-row {{ display: flex; gap: 12px; margin: 16px 0; }}
.kpi-card {{ flex: 1; padding: 16px; border-radius: 8px; color: white; text-align: center; }}
.kpi-value {{ font-size: 32px; font-weight: bold; }}
.kpi-label {{ font-size: 13px; opacity: 0.9; }}
.chart-row {{ display: flex; gap: 12px; }}
.chart-col {{ flex: 1; }}
</style></head><body>
<h1>MemALL Federation Health Report</h1>
<p>生成时间: {now} | 数据源: ~/.memall/family.db</p>
<img src="data:image/png;base64,{kpi_img}" style="width:100%">
<div class="chart-row">
  <div class="chart-col"><img src="data:image/png;base64,{agent_img}" style="width:100%"></div>
  <div class="chart-col"><img src="data:image/png;base64,{pie_img}" style="width:100%"></div>
</div>
<img src="data:image/png;base64,{trend_img}" style="width:100%">
{detail_section}
</body></html>"""


def generate_report(output_path: str = "", format: str = "html", detail: bool = False) -> dict:
    _ensure_output_dir()
    stats = federation_health(detail=detail)

    kpi_img = _plot_kpi_cards(stats)
    agent_img = _plot_agent_distribution(stats)
    pie_img = _plot_conflict_pie(stats)
    trend_img = _plot_trend(stats)

    now = datetime.now()
    if not output_path:
        output_path = str(OUTPUT_DIR / f"federation_report_{now.strftime('%Y%m%d_%H%M%S')}.{format}")

    # Security: restrict output path to allowed directory
    out = Path(output_path).with_suffix(f".{format}" if format == "png" else ".html").resolve()
    allowed_dir = OUTPUT_DIR.resolve()
    if not str(out).startswith(str(allowed_dir)):
        # Fall back to default output directory
        out = allowed_dir / out.name
        output_path = str(out)

    if format == "png":
        fig, axes = plt.subplots(2, 2, figsize=(12, 9))
        axes = axes.flatten()
        out_png = out.with_suffix(".png")
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return {"path": str(out_png), "format": "png", "total": stats["total"]}

    html = _build_html(kpi_img, agent_img, pie_img, trend_img, stats, detail=detail)
    out.write_text(html, encoding="utf-8")
    return {"path": str(out), "format": "html", "total": stats["total"]}