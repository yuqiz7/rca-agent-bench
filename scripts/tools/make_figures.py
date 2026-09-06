#!/usr/bin/env python3
"""make_figures.py -- redraw the figures README embeds.

One figure today: docs/figures/arms_holdout16.png, the three arms on the 16-card
holdout set. Every value comes from readme_check.compute(), the same function the
README markers are generated from, so a figure cannot disagree with the sentence
next to it.

Run it directly, or let `readme_check.py --write` call it. matplotlib is not part
of the CI dependency set: nothing in the gates draws, and --check only compares
text, so a missing matplotlib is a warning there and an error here.

    python scripts/tools/make_figures.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FIGURES = os.path.join(REPO, "docs", "figures")
ARMS_PNG = os.path.join(FIGURES, "arms_holdout16.png")

# Two greys and one dark accent: the eye should land on the agent bar and nowhere
# else. Text is near-black rather than black to sit calmly on white.
GREY, GREY_DARK, ACCENT, INK = "#c3c9ce", "#9aa3ab", "#1f4e5f", "#222222"
ARMS = [("rules, no model", "rules", GREY),
        ("single-shot LLM", "single_shot", GREY_DARK),
        ("agent", "agent", ACCENT)]


def _pct(text):
    return float(re.search(r"([0-9.]+)%", text).group(1))


def _usd(text):
    return float(re.search(r"\$([0-9.]+)", text).group(1))


def arms_holdout16(values):
    """Left: top-1 per arm. Right: the same three arms as cost against accuracy."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [(label, _pct(values[f"holdout16.{key}.top1"]),
             _usd(values[f"holdout16.{key}.cost"]), color)
            for label, key, color in ARMS]

    fig, (bar, scat) = plt.subplots(
        1, 2, figsize=(10, 3.4), dpi=200,
        gridspec_kw={"width_ratios": [1.7, 1.0], "wspace": 0.35})
    fig.patch.set_facecolor("white")

    ys = range(len(rows))
    bar.barh(list(ys), [r[1] for r in rows], color=[r[3] for r in rows], height=0.55)
    for y, (label, pct, _cost, color) in zip(ys, rows):
        bar.text(pct + 1.2, y, f"{pct:.1f}%", va="center", ha="left",
                 color=color if color != GREY else GREY_DARK, fontsize=11,
                 fontweight="bold" if color == ACCENT else "normal")
    bar.set_yticks(list(ys), [r[0] for r in rows], fontsize=10, color=INK)
    bar.invert_yaxis()
    bar.set_xlim(0, 100)
    bar.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100%"])
    bar.set_xlabel("top-1 accuracy, 16 unseen cards", fontsize=9, color=INK)
    bar.tick_params(axis="x", labelsize=8, colors=INK)
    for side in ("top", "right", "left"):
        bar.spines[side].set_visible(False)
    bar.spines["bottom"].set_color("#dddddd")

    for label, pct, cost, color in rows:
        scat.scatter([cost], [pct], s=70, color=color, zorder=3)
        scat.annotate(label, (cost, pct), textcoords="offset points",
                      xytext=(8, -3), fontsize=8.5, color=INK)
    scat.set_title("accuracy per dollar", fontsize=10, color=INK, loc="left")
    scat.set_xlabel("$ per diagnosis", fontsize=9, color=INK)
    scat.set_ylabel("top-1 %", fontsize=9, color=INK)
    scat.set_xlim(-0.012, 0.115)
    scat.set_xticks([0.00, 0.05, 0.10], ["$0", "$0.05", "$0.10"])
    scat.set_ylim(40, 85)
    scat.tick_params(labelsize=8, colors=INK)
    for side in ("top", "right"):
        scat.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        scat.spines[side].set_color("#dddddd")

    os.makedirs(FIGURES, exist_ok=True)
    fig.savefig(ARMS_PNG, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return ARMS_PNG


def regenerate(values):
    return [arms_holdout16(values)]


def main():
    sys.path.insert(0, HERE)
    import readme_check
    for path in regenerate(readme_check.compute()):
        print(f"wrote {os.path.relpath(path, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
