# Delivery notes

## Deletion manifest

**A zip cannot express a deletion.** Extracting over an existing checkout adds
and overwrites; it never removes. Files dropped from the package therefore stay
on disk and keep being picked up by globs. That is not hypothetical: five
superseded experiments survived three extractions this way, one of them ran on
every verification pass, and — because the allocator it builds has no ordering
point to register its leases with — it admitted nothing, passed its oracle on an
empty sample, and exited 0.

Apply these by hand after extracting. Each is a path removed from the package
relative to the previous one.

### Package of 2026-09-30 (after approval)

No deletions. Added: `marginstream/scenarios.py`, `experiments/e8_multifactor.py`,
`experiments/e9_flash_crash.py`, their two result files, `paper/A_D_mechanism.md`,
`paper/A_E_figures.md`, and the nine rendered figures in `paper/figures/` (taken
from the v6 PDF, so the build no longer needs a browser). The algebra of §2.3–2.4
moved to Appendix D; the liquidation figure moved to Appendix E, so figures 4 and
5 are renumbered 3 and 4. `paper/assemble_whitepaper.py` now resolves its paths
from its own location and writes to `build/`.

### Package of 2026-09-16b (equations)

No deletions. The equations in §2.3, §2.4 and §5.4 are LaTeX math rather than
fixed-width text, and the symbols quoted inline alongside them match. GitHub
renders `$...$` and `$$...$$` in markdown, so the sources still read correctly
there. `paper/MarginStream_whitepaper.tex` is the generated LaTeX, added so a
teammate can see what the build produces; it is **generated, not source** —
edit the markdown and re-run `paper/assemble_whitepaper.py`.

### Package of 2026-09-16 (figures and page budget)

    git rm paper/B_target_deployment.md

Figure 5 moved into `paper/diagrams.md` as the deployment view, which §2 of the
capstone brief requires in the body rather than an appendix. Appendix B no longer
exists; §5.7, §7 and §9 now point at Figure 5.

New in this package: `paper/figures/` (nine rendered panels, PDF and SVG),
`paper/assemble_whitepaper.py` (markdown to LaTeX, figures embedded) and
`paper/MarginStream_whitepaper.pdf`.

### Previous package

    git mv results/e1_safety.json       results/superseded/e1_safety.json
    git mv results/e1_worst_fill.json   results/superseded/e1_worst_fill.json
    git mv results/e2_negative.json     results/superseded/e2_negative.json
    git mv results/e4_conditional.json  results/superseded/e4_conditional.json
    git mv results/e5_adversarial.json  results/superseded/e5_adversarial.json
    git rm WHITEPAPER_SKELETON.md

Extracting this package already places the five JSON files under
`results/superseded/`, so the `git mv` lines are only needed to remove the copies
left at the old paths. Check first:

    git ls-files results/ | grep -v superseded

should list seven `.json` files and `PROVENANCE.md`, nothing else.

### Previous package, applied 2026-09-01

    git rm experiments/e1_safety.py experiments/e1_worst_fill_safety.py \
           experiments/e2_negative.py experiments/e4_conditional.py \
           experiments/e5_adversarial.py

## Verifying a package

Extract into a **new** directory. A new directory reflects what the package
actually contains; extracting over a checkout never does.

    rm -rf ~/Projects/marginstream_check
    mkdir ~/Projects/marginstream_check && cd ~/Projects/marginstream_check
    unzip -q ~/Downloads/marginstream.zip

Then run the enumerated commands in `README.md` — not a glob, which is how a
stale file re-enters the verification set. Once the run is clean, apply the
manifest above in the real checkout and extract there.

## What changed in this package

- `README.md` rewritten. It described the withdrawn price-conditional schedule
  and told the reader to run four files that no longer exist at those paths.
- `WHITEPAPER_SKELETON.md` removed; it carried the same withdrawn model, and
  `README.md` plus `paper/` now cover what it was for.
- `results/` split: seven current files re-recorded in one session with
  `PROVENANCE.md` giving machine, OS, Python, date and commit; five older files
  moved to `results/superseded/` with the commit that produced each.
- `paper/01` §1.7 no longer quotes absolute nanoseconds for E3, only the ratios
  in the recorded file.
- `paper/02` §2.3 separates `G_k` from `G+`, and states the model boundary: every
  correctness experiment uses a seven-point single-factor grid.
- `paper/06` §6.1 corrects the compromised-gateway bound; NFR rows 10 and 11 are
  marked as targets; §4.4's withdrawal posting no longer books two debits in one
  entry; §5.5 is split by tier and no longer claims warm failover meets its
  budget.
- Figures 1 and 2 carry the authority registration and the ordering point's
  binding checks.

No file under `marginstream/`, `tests/` or `experiments/` changed. `results/`
changed by re-recording and archiving, which is the point of the round.

## Page budget

The brief allows **≤ 20 pages + appendices**. Item 9 of its required contents is
itself named "AI-disclosure appendix", so the body that counts is §1–§8.

    §1-§8    20 pages
    §9        1 page
    A, C      4 pages
    total    25 pages

§1–§8 is at the limit, not over it. If the grader counts §9 in the body it is 21,
so the first cut if one is needed is §9 moved behind the appendix divider, which
is where its own title says it belongs.

## Outstanding before submission

1. **`paper/09` §9.5 ownership tables are `TODO`.** Four members, nine sections
   and four modules. This cannot be filled in from here and **must not reach the
   PDF as `TODO`**.
2. **Figures are rendered and embedded.** Mermaid CLI with a headless browser,
   native SVG labels rather than HTML, Times to match the body. Figures 2 and 3
   are split into panels because the single-chain versions came out 35cm and
   70cm tall at column width.
3. **The PDF is built and the figures are rendered.** `paper/MarginStream_whitepaper.pdf`
   is the current build; `paper/assemble_whitepaper.py` regenerates it from the
   markdown. Smallest figure label is 6.2pt after scaling.
4. **Course-pack cross-references unverified.** The running-case citations
   (Part 2 §3, Part 3 §1/§4/§7, Part 5 §1) have not been checked against the
   source material.
