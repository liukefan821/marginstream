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

### Package of 2026-10-01 (professor feedback: legibility)

No deletions. Added `paper/00_executive_summary.md` (executive summary, first
page). §5.4 maps the Session 3 timeline to tested and design-only responses;
§4.2 and §4.4 state each ledger account's normal side and write every entry as
Dr/Cr; §2.2, §9.4 and Appendix D corrected; §3.1 separates displays (don't-care),
money-authorising balances (strong) and retry handling; §6.2 states the
withdrawal ceremony. To keep the body to 20 pages, §6.1's full argument moved to
the new `paper/A_F_trust_boundaries.md` (Appendix F), the E6 and E7 detail of
§5.4 moved to Appendix E.2, §1.6–1.7 and §8.4 were compressed, §2.8 was removed
Figures 1–3 are scaled to 65%, 65% and 55% width and Figure 5 to 16 cm, and
Figure 4 is redrawn in TikZ (`paper/figures/fig5_deployment.tex`) so no label
overlaps; every figure is pinned to its own text, and the markdown's `---`
separators no longer print as rules. Later corrections: §2.2 separates the
capacity cost of a wider shock range from the time cost of more scenarios; the
executive summary says atomic basket transfers; §9.5 lists owners for
Appendices E and F (there is no Appendix B, which was folded into §2.9).
`paper/assemble_whitepaper.py` builds on macOS and with pandoc 3. `experiments/e9_flash_crash.py` changed in its
docstring only; every result file is unchanged.

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

The brief allows ≤ 20 pages + appendices; the body that counts is the executive
summary and §1–§8.

    Summary + §1-§8   20 pages
    §9                  1 page
    Appendices          8 pages

## Status before submission

1. §9.5 ownership is filled in.
2. The PDF is rebuilt from the markdown with `paper/assemble_whitepaper.py`.
3. The running-case citations (Part 2 §3, Part 3 §1/§4/§7, Part 4 §2–3,
   Part 5 §1) were checked against the OrderStream case pack on 2026-10-01.
