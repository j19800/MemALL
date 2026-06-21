from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx

try:
    from pyvis.network import Network
    _HAS_PYVIS = True
except ImportError:
    _HAS_PYVIS = False

from memall.core.db import get_conn

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "SimSun"]
plt.rcParams["axes.unicode_minus"] = False

REPORT_DIR = Path.home() / ".memall" / "reports"
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _get_graph_data(conn, center_id: int = None, limit: int = None):
    if center_id:
        two_hop = set()
        two_hop.add(center_id)
        edges = conn.execute(
            "SELECT source_id, target_id, relation_type, weight FROM edges WHERE source_id = ? OR target_id = ? LIMIT 5000",
            (center_id, center_id),
        ).fetchall()
        for e in edges:
            two_hop.add(e["source_id"])
            two_hop.add(e["target_id"])
        second_ids = list(two_hop - {center_id})
        if second_ids:
            placeholders = ",".join("?" * len(second_ids))
            edges2 = conn.execute(
                f"SELECT source_id, target_id, relation_type, weight FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders}) LIMIT 10000",
                tuple(second_ids * 2),
            ).fetchall()
            edges = list(edges) + list(edges2)
            for e in edges2:
                two_hop.add(e["source_id"])
                two_hop.add(e["target_id"])
        mem_ids = list(two_hop)
    else:
        if limit:
            mem_ids = [r[0] for r in conn.execute(
                "SELECT id FROM memories ORDER BY access_count DESC LIMIT ?", (limit,)
            ).fetchall()]
        else:
            mem_ids = [r[0] for r in conn.execute("SELECT id FROM memories").fetchall()]
        edges = conn.execute(
            f"SELECT source_id, target_id, relation_type, weight FROM edges WHERE source_id IN ({','.join('?'*len(mem_ids))}) AND target_id IN ({','.join('?'*len(mem_ids))}) LIMIT 20000",
            tuple(mem_ids * 2),
        ).fetchall()

    mem_map = {}
    mem_set = set(mem_ids)
    for r in conn.execute(
        "SELECT id, content, category FROM memories WHERE id IN ({})".format(
            ",".join("?" * len(mem_set))
        ),
        tuple(mem_set),
    ).fetchall():
        mem_map[r["id"]] = {"content": r["content"][:80], "category": r["category"] or "general"}

    return mem_map, edges, mem_ids


def _node_color(category: str) -> str:
    cats = sorted(set(m["category"] for m in [{"category": category}]))
    idx = hash(category) % len(COLORS) if category else 0
    return COLORS[idx]


def _build_graph(mem_map: dict, edges: list):
    G = nx.Graph()
    for mid, info in mem_map.items():
        G.add_node(mid, label=info["content"][:30], title=info["content"], category=info["category"])
    for e in edges:
        if e["source_id"] in mem_map and e["target_id"] in mem_map:
            G.add_edge(e["source_id"], e["target_id"], weight=e["weight"] or 1.0, type=e["relation_type"])
    return G


def visualize_png(G: nx.Graph, output_path: str, layout: str = "spring"):
    fig, ax = plt.subplots(figsize=(16, 12))
    layouts = {"spring": nx.spring_layout, "circular": nx.circular_layout, "kamada": nx.kamada_kawai_layout}
    pos = layouts.get(layout, nx.spring_layout)(G, k=2, iterations=50, seed=42)

    categories = set(nx.get_node_attributes(G, "category").values())
    cat_colors = {c: COLORS[i % len(COLORS)] for i, c in enumerate(sorted(categories))}
    node_colors = [cat_colors.get(G.nodes[n].get("category", ""), "#ccc") for n in G.nodes]
    degrees = dict(G.degree())
    node_sizes = [max(20, min(200, degrees[n] * 2)) for n in G.nodes]

    weights = [G.edges[e].get("weight", 1) for e in G.edges]
    edge_widths = [max(0.3, min(3, w * 0.5)) for w in weights]

    nx.draw_networkx_edges(G, pos, alpha=0.3, width=edge_widths, ax=ax)
    nx.draw_networkx_nodes(G, pos, node_size=node_sizes, node_color=node_colors, alpha=0.8, ax=ax)
    labels = {n: G.nodes[n].get("label", str(n)) for n in G.nodes}
    nx.draw_networkx_labels(G, pos, labels, font_size=6, ax=ax)

    legend_elements = []
    for cat, color in sorted(cat_colors.items()):
        legend_elements.append(plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=color,
                                          markersize=10, label=cat[:20]))
    ax.legend(handles=legend_elements, loc="upper right", fontsize=8, title="Category")
    ax.set_title(f"Memory Graph — {G.number_of_nodes()} nodes, {G.number_of_edges()} edges", fontsize=14)
    ax.axis("off")
    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def visualize_html(G: nx.Graph, output_path: str, layout: str = "spring"):
    if not _HAS_PYVIS:
        raise RuntimeError("pyvis not installed. Run: pip install pyvis jsonpickle ipython")
    net = Network(height="800px", width="100%", directed=False, bgcolor="#f5f5f5", font_color="#333")
    net.barnes_hut()

    cat_colors = {}
    for n, data in G.nodes(data=True):
        cat = data.get("category", "")
        if cat not in cat_colors:
            cat_colors[cat] = COLORS[len(cat_colors) % len(COLORS)]
        net.add_node(n, label=data.get("label", str(n))[:25], title=f"#{n}: {data.get('title','')}",
                     color=cat_colors[cat], size=10 + min(30, G.degree(n) * 2))

    for u, v, data in G.edges(data=True):
        net.add_edge(u, v, value=data.get("weight", 1), title=data.get("type", ""))

    net.show_buttons(filter_=["physics"])
    net.save_graph(output_path)
    return output_path


def generate_graph(center_id: int = None, limit: int = None, format: str = "html",
                   layout: str = "spring", output_path: str = "") -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        mem_map, edges, mem_ids = _get_graph_data(conn, center_id=center_id, limit=limit)
        G = _build_graph(mem_map, edges)

        now = datetime.now()
        if not output_path:
            suffix = "_center" + str(center_id) if center_id else ""
            output_path = str(REPORT_DIR / f"memory_graph_{now.strftime('%Y%m%d_%H%M%S')}{suffix}.{format}")

        if format == "png":
            path = visualize_png(G, str(Path(output_path).with_suffix(".png")), layout=layout)
        else:
            path = visualize_html(G, str(Path(output_path).with_suffix(".html")), layout=layout)

        return {
            "path": path,
            "format": format,
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "center_id": center_id,
        }
    finally:
        conn.close()
