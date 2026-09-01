# Exporting the diagrams for the PDF

GitHub renders the Mermaid blocks in `paper/diagrams.md` inline, so the repo
view needs no extra step. The whitepaper PDF does.

Per figure:

1. Open https://mermaid.live
2. Clear the left-hand editor pane (select all, delete).
3. From `paper/diagrams.md`, copy the block between the ```mermaid fence and
   the closing fence, not including the fences themselves.
4. Paste into the left pane. The diagram renders on the right.
5. Below the right pane, click **Actions** then **SVG**. The file downloads to
   ~/Downloads.
6. Rename it and move it in:

       mkdir -p paper/figures
       mv ~/Downloads/mermaid-diagram-*.svg paper/figures/fig1_component.svg

   Repeat with fig2_dataflow.svg, fig3_liquidation.svg, fig4_failure.svg,
   and fig5_deployment.svg from `paper/B_target_deployment.md`.

Check after all four:

    ls paper/figures/

Expect five files: four from `paper/diagrams.md` and one from Appendix B. If a paste renders as an error message rather than a
diagram, the block was copied with a fence line included.

The five blocks have been checked structurally — correct diagram kind, no stray
fences, `<` escaped as `&lt;` inside labels — but they have not been rendered in
this environment. mermaid.live is the first place they render, so check all five
before exporting.

For the PDF, SVG keeps text selectable and scales without blurring. If the
document toolchain cannot place SVG, export PNG from the same Actions menu at
2x scale rather than screenshotting the browser.
