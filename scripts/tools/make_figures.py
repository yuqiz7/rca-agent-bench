#!/usr/bin/env python3
"""make_figures.py -- redraw the figures README embeds.

Two figures: arms_holdout16.png, the three arms on the 16-card holdout set, and
pipeline.png, the card production line. Every value comes from
readme_check.compute(), the same function the README markers are generated from,
so a figure cannot disagree with the sentence next to it.

The pipeline is a drawing rather than a mermaid block because mermaid-cli cannot
launch a browser on this host, and an unverifiable diagram that GitHub might fail
to render is worse than a png.

Run it directly, or let `readme_check.py --write` call it. matplotlib is not part
of the CI dependency set: nothing in the gates draws, and --check only compares
text, so a missing matplotlib is a warning there and an error here.

    python scripts/tools/make_figures.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FIGURES = os.path.join(REPO, "docs", "figures")
ARMS_PNG = os.path.join(FIGURES, "arms_holdout16.png")
PIPELINE_PNG = os.path.join(FIGURES, "pipeline.png")

# Two greys and one dark accent: the eye should land on the agent bar and nowhere
# else. Text is near-black rather than black to sit calmly on white.
GREY_LIGHT, GREY, GREY_DARK = "#dfe3e6", "#c3c9ce", "#9aa3ab"
ACCENT, INK = "#1f4e5f", "#222222"

# Five arms. The two stock-configuration arms share the lightest grey: they are one
# configuration, not two independent results (evidence_audit C7). Hollow marker on
# agent-haiku in the scatter, because that point is the one worth looking at.
# Hand-placed so the two arms that land on the same accuracy do not overlap.
LABEL_OFFSET = {"single_shot_sonnet": (7, -12), "agent_haiku": (7, 7),
                "single_shot_haiku": (7, 4)}
ARM_STYLE = {"rules": (GREY, True),
             "single_shot_sonnet": (GREY_DARK, True),
             "single_shot_haiku": (GREY_LIGHT, True),
             "agent_haiku": (GREY_LIGHT, False),
             "agent_sonnet": (ACCENT, True)}


def arms_holdout16(values):
    """Left: top-1 per arm. Right: the same three arms as cost against accuracy.

    Arm metrics come straight from readme_check.report_table, the same parse the
    README's tables are built from."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sys.path.insert(0, HERE)
    import readme_check
    table = readme_check.report_table("fivearm_holdout16_20260906")
    rows = sorted(((key, readme_check.ARM_LABEL[key], readme_check.pct_of(m["top1"]),
                    readme_check.usd_of(m["cost"])) for key, m in table.items()),
                  key=lambda r: r[2])

    fig, (bar, scat) = plt.subplots(
        1, 2, figsize=(10, 3.9), dpi=200,
        gridspec_kw={"width_ratios": [1.7, 1.0], "wspace": 0.35})
    fig.patch.set_facecolor("white")

    ys = range(len(rows))
    bar.barh(list(ys), [r[2] for r in rows],
             color=[ARM_STYLE[r[0]][0] for r in rows], height=0.55)
    for y, (key, _label, pct, _cost) in zip(ys, rows):
        color = ARM_STYLE[key][0]
        bar.text(pct + 1.2, y, f"{pct:.1f}%", va="center", ha="left",
                 color=color if color == ACCENT else GREY_DARK, fontsize=10.5,
                 fontweight="bold" if color == ACCENT else "normal")
    bar.set_yticks(list(ys), [r[1] for r in rows], fontsize=9, color=INK)
    bar.invert_yaxis()
    bar.set_xlim(0, 100)
    bar.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100%"])
    bar.set_xlabel("top-1 accuracy, 16 unseen cards", fontsize=9, color=INK)
    bar.tick_params(axis="x", labelsize=8, colors=INK)
    for side in ("top", "right", "left"):
        bar.spines[side].set_visible(False)
    bar.spines["bottom"].set_color("#dddddd")

    for key, label, pct, cost in rows:
        color, filled = ARM_STYLE[key]
        scat.scatter([cost], [pct], s=70, zorder=3,
                     color=color if filled else "none",
                     edgecolors=GREY_DARK if not filled else color,
                     linewidths=1.4 if not filled else 0)
        scat.annotate(label, (cost, pct), textcoords="offset points",
                      xytext=LABEL_OFFSET.get(key, (7, -3)), fontsize=7.5, color=INK)
    scat.text(0.42, 0.05, "cheaper model, costlier agent", transform=scat.transAxes,
              fontsize=7.5, color=GREY_DARK, style="italic")
    scat.set_title("accuracy per dollar", fontsize=10, color=INK, loc="left")
    scat.set_xlabel("$ per diagnosis", fontsize=9, color=INK)
    scat.set_ylabel("top-1 %", fontsize=9, color=INK)
    scat.set_xlim(-0.012, 0.125)
    scat.set_xticks([0.00, 0.04, 0.08, 0.12], ["$0", "$0.04", "$0.08", "$0.12"])
    scat.set_ylim(18, 85)
    scat.tick_params(labelsize=8, colors=INK)
    for side in ("top", "right"):
        scat.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        scat.spines[side].set_color("#dddddd")

    os.makedirs(FIGURES, exist_ok=True)
    fig.savefig(ARMS_PNG, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return ARMS_PNG


def pipeline(values):
    """The production line, one box per stage, left to right."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrow, FancyBboxPatch

    stages = [
        ("Testbed", values["containers"]),
        ("Fault primitives", "{}\n{}".format(values["primitives"], values["classes"])),
        ("Scenario cards", "{}\n{}".format(values["recipe_cards"], values["instock"])),
        ("Probe verdicts", values["probes"]),
        ("Evidence packs", values["evidence_files"]),
        ("Three arms", "one pack, offline"),
        ("Harness", "one grader"),
        ("Findings", "docs/findings.md"),
    ]
    fig, ax = plt.subplots(figsize=(13, 1.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, len(stages))
    ax.set_ylim(0, 1)
    ax.axis("off")

    for i, (title, sub) in enumerate(stages):
        accent = title == "Three arms"
        box = FancyBboxPatch((i + 0.06, 0.24), 0.80, 0.52,
                             boxstyle="round,pad=0.01,rounding_size=0.04",
                             linewidth=1.1, facecolor=ACCENT if accent else "#f4f6f7",
                             edgecolor=ACCENT if accent else "#c3c9ce")
        ax.add_patch(box)
        ax.text(i + 0.46, 0.60, title, ha="center", va="center", fontsize=7.4,
                color="white" if accent else INK, fontweight="bold")
        ax.text(i + 0.46, 0.40, sub, ha="center", va="center", fontsize=6.5,
                linespacing=1.5, color="#e8eef0" if accent else "#5b6770")
        if i < len(stages) - 1:
            ax.add_patch(FancyArrow(i + 0.88, 0.50, 0.09, 0, width=0.005,
                                    head_width=0.06, head_length=0.05,
                                    length_includes_head=True, color="#9aa3ab"))

    os.makedirs(FIGURES, exist_ok=True)
    fig.savefig(PIPELINE_PNG, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return PIPELINE_PNG


def regenerate(values):
    return [arms_holdout16(values), pipeline(values)]


def main():
    sys.path.insert(0, HERE)
    import readme_check
    for path in regenerate(readme_check.compute()):
        print(f"wrote {os.path.relpath(path, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
