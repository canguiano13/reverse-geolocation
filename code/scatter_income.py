import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from scipy import stats

schools = pd.read_csv("data/outputs/schools_with_income.csv")
coverage = pd.read_csv("data/outputs/school_coverage.csv")

coverage["found"] = (coverage["high"] > 0).astype(int)
df = schools.merge(coverage[["school_name", "found"]], on="school_name", how="left")
df["found"] = df["found"].fillna(0).astype(int)
df = df.dropna(subset=["county_median_household_income"]).copy()
df["income_k"] = df["county_median_household_income"] / 1000

rho, pval = stats.spearmanr(df["income_k"], df["found"])

np.random.seed(42)
jitter = np.random.uniform(-0.07, 0.07, size=len(df))

fig, ax = plt.subplots(figsize=(5, 3.5))

not_found = df[df["found"] == 0]
found = df[df["found"] == 1]

nf_idx = df[df["found"] == 0].index
f_idx = df[df["found"] == 1].index

ax.scatter(df.loc[nf_idx, "income_k"],
           0 + jitter[:len(nf_idx)],
           color="#9ecae1", alpha=0.7, s=25, label="Not found", zorder=2)
ax.scatter(df.loc[f_idx, "income_k"],
           1 + jitter[len(nf_idx):len(nf_idx)+len(f_idx)],
           color="#2171b5", alpha=0.9, s=35, label="Found", zorder=3)

ax.set_yticks([0, 1])
ax.set_yticklabels(["Not found", "Found"])
ax.set_xlabel("County median household income ($000s)")
ax.set_ylim(-0.4, 1.4)
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))

pval_str = "$p < 0.0001$" if pval < 0.0001 else f"$p = {pval:.4f}$"
ax.text(0.97, 0.05, f"$\\rho = {rho:.3f}$, {pval_str}\n$n = {len(df)}$",
        transform=ax.transAxes, ha="right", va="bottom", fontsize=8,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.8))

ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.legend(loc="upper left", fontsize=8, framealpha=0.8)

plt.tight_layout()
plt.savefig("/reverse-geolocation/figures/income_scatter.png", dpi=150, bbox_inches="tight")
print(f"Saved. rho={rho:.3f}, p={pval:.4e}, n={len(df)}")
print(f"Found: {df['found'].sum()}, Not found: {(df['found']==0).sum()}")
