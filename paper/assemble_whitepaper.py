"""Assemble paper/*.md into one LaTeX document and report the page count.

Mermaid blocks are replaced by a framed box of the height given in FIG_HEIGHT,
so the count includes the space five rendered figures will occupy. Those heights
are estimates and are the main source of error in the total; they are declared
here rather than buried so they can be changed in one place.
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "build")

ORDER = [
    ("00_executive_summary.md", None),
    ("01_context_and_requirements.md", None),
    ("02_architecture.md", None),
    ("diagrams.md", None),
    ("03_consistency_map.md", None),
    ("04_data_and_storage.md", None),
    ("05_failure_and_recovery.md", None),
    ("06_security_and_threat_model.md", None),
    ("07_tradeoffs_and_alternatives.md", None),
    ("08_operations.md", None),
    ("09_ai_disclosure.md", None),
    ("A_decision_history.md", None),
    ("C_protocols.md", None),
    ("A_D_mechanism.md", None),
    ("A_E_figures.md", None),
    ("A_F_trust_boundaries.md", None),
]

# height reserved for each rendered mermaid figure, in order of appearance
# measured from the rendered diagrams at a 17cm column
FIG_HEIGHT = {
    1: "12.6cm",   # fig 1  component view
    2: "19.1cm",   # fig 2  per-order flow, two panels side by side
    3: "15.4cm",   # fig 3  liquidation, three panels side by side
    4: "12.2cm",   # fig 4  degradation ladder
    5: "9.1cm",    # fig 5  target deployment, appendix B
}

fig_counter = [0]

CAPTION = {
    1: "Component view. Two authorities partitioned on different keys, and one "
       "ordering point both of them and every recovery path read from. The "
       "thick edge is the lease registration this design would be unsound "
       "without.",
    2: "Data flow for one order, in three stages. The first two are the "
       "gateway's and nothing downstream re-derives them; the third is the "
       "ordering point's and a compromised gateway cannot influence it. "
       "Refusals are collapsed to one node per stage; the reason codes are "
       "listed in the sections cited.",
    3: "Liquidation and settlement. Stop, unwind, settle. The two exits from "
       "the cancel check are the two different facts of \\S5.4.",
    4: "Failure paths and the degradation ladder. The four edges into FENCED "
       "are the four ways this design loses authority, and all four fail "
       "closed for new risk.",
    5: "Deployment view. The target deployment; no part of it has been "
       "implemented or measured.",
}

# Each figure is laid out so its smallest label stays legible after scaling.
# Rendered at 32px in mermaid; the effective size in the PDF is that times the
# scale factor, and anything under about 6.5pt stops being readable in print.
# The source has one fenced block per panel, so BLOCK_OF maps block index to the
# figure it belongs to and only the first block of a figure emits anything.
BLOCK_OF = [1, 2, 2, 2, 4, 5, 3, 3, 3]

BLOCK = {
    1: r"\includegraphics[width=0.65\linewidth]{fig1_components}",
    2: (r"\includegraphics[width=0.65\linewidth]{fig2a}\\[0.6em]"
        r"\includegraphics[width=0.65\linewidth]{fig2b}\\[0.6em]"
        r"\includegraphics[width=0.65\linewidth]{fig2c}"),
    3: (r"\includegraphics[height=16cm]{fig3a}\hfill"
        r"\includegraphics[height=16cm]{fig3b}\hfill"
        r"\includegraphics[height=16cm]{fig3c}"),
    4: r"\includegraphics[width=0.55\linewidth]{fig4_ladder}",
    5: r"\includegraphics[width=0.85\linewidth]{fig5_deployment}",
}


def figure_block(n):
    return ("\n```{=latex}\n"
            "\\begin{figure}[H]\\centering\n"
            "%s\n\\caption{%s}\n\\end{figure}\n```\n") % (BLOCK[n], CAPTION[n])


def strip_mermaid(md):
    """Replace each mermaid fence with the rendered figure."""
    def repl(_m):
        i = fig_counter[0]
        fig_counter[0] += 1
        n = BLOCK_OF[i] if i < len(BLOCK_OF) else None
        first = n is not None and (i == 0 or BLOCK_OF[i - 1] != n)
        return figure_block(n) if first else "\n"
    return re.sub(r"```mermaid\n.*?\n```", repl, md, flags=re.S)


SUPERSCRIPT = {"\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
               "\u2074": "4", "\u2075": "5", "\u2076": "6", "\u2077": "7",
               "\u2078": "8", "\u2079": "9", "\u207b": "-"}


def fix_glyphs(md):
    """Characters TeX Gyre Termes does not carry, which xelatex drops in
    silence. The superscript digits are the dangerous ones: 10\u2076 prints
    as 10."""
    out = []
    i = 0
    while i < len(md):
        if md[i] in SUPERSCRIPT:
            run = ""
            while i < len(md) and md[i] in SUPERSCRIPT:
                run += SUPERSCRIPT[md[i]]
                i += 1
            out.append("`\\textsuperscript{%s}`{=latex}" % run)
            continue
        if md[i] == "\u2194":
            out.append("`$\\leftrightarrow$`{=latex}")
            i += 1
            continue
        out.append(md[i])
        i += 1
    return "".join(out)


def convert(path):
    md = open(os.path.join(REPO, "paper", path)).read()
    md = strip_mermaid(md)
    # the markdown's "---" separators read well on GitHub but print as stray rules
    md = re.sub(r"^---\s*$", "", md, flags=re.M)
    md = fix_glyphs(md)
    # demote headings by one level so file-level "#" becomes \section
    md = re.sub(r"^(#+) ", lambda m: "#" * (len(m.group(1)) + 1) + " ", md,
                flags=re.M)
    p = subprocess.run(
        ["pandoc", "-f", "markdown+pipe_tables+raw_attribute+tex_math_dollars",
         "-t", "latex",
         "--wrap=preserve"],
        input=md, capture_output=True, text=True)
    if p.returncode != 0:
        print("pandoc failed on", path, file=sys.stderr)
        print(p.stderr[:800], file=sys.stderr)
        sys.exit(1)
    return p.stdout


PREAMBLE = r"""\documentclass[10pt,a4paper]{article}

\usepackage[top=1.9cm,bottom=1.9cm,left=2.0cm,right=2.0cm]{geometry}
\usepackage{fontspec}
\setmainfont{texgyretermes-regular.otf}[
  BoldFont=texgyretermes-bold.otf,
  ItalicFont=texgyretermes-italic.otf,
  BoldItalicFont=texgyretermes-bolditalic.otf]
\setmonofont{texgyrecursor-regular.otf}[
  BoldFont=texgyrecursor-bold.otf,
  ItalicFont=texgyrecursor-italic.otf,
  Scale=0.82]
\usepackage{amsmath,amssymb}
\setlength{\abovedisplayskip}{5pt}\setlength{\belowdisplayskip}{5pt}
\setlength{\abovedisplayshortskip}{3pt}\setlength{\belowdisplayshortskip}{3pt}
\usepackage{longtable,booktabs,array,calc}
\usepackage{etoolbox}
\usepackage{graphicx}
\usepackage{float}  % [H]: a figure stays where its text puts it
\usepackage[export]{adjustbox}
\usepackage{caption}
\captionsetup{font=small,labelfont=bf,skip=4pt}
\usepackage{enumitem}
\setlist{nosep,leftmargin=1.4em}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}
\titlespacing*{\section}{0pt}{1.1em}{0.5em}
\titlespacing*{\subsection}{0pt}{0.8em}{0.35em}
\titlespacing*{\subsubsection}{0pt}{0.6em}{0.25em}
\titleformat{\section}{\large\bfseries}{}{0pt}{}
\titleformat{\subsection}{\normalsize\bfseries}{}{0pt}{}
\titleformat{\subsubsection}{\normalsize\itshape}{}{0pt}{}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.32em}
\linespread{1.0}
\usepackage{fancyhdr}
\pagestyle{fancy}\fancyhf{}
\fancyhead[L]{\small MarginStream}
\fancyhead[R]{\small SC6118 Capstone --- Group 1}
\fancyfoot[C]{\small\thepage}
\renewcommand{\headrulewidth}{0.3pt}

\AtBeginEnvironment{longtable}{\small}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\newcommand{\passthrough}[1]{#1}
\newcounter{none}  % pandoc 3 tables reference it

\begin{document}

\begin{center}
{\LARGE\bfseries MarginStream}\\[0.4em]
{\large A cross-margin derivatives venue with separated margin and matching
authorities}\\[0.8em]
{\normalsize SC6118 Scalable Systems Architecture for Fintech --- Capstone,
Group 1}\\[0.3em]
{\small LIU KEFAN \quad ZHOU CONGXIANG \quad WU YOUJHEN \quad ZHANG HAOXI}
\end{center}

\vspace{0.6em}
"""

body = []
for i, (path, _) in enumerate(ORDER):
    if path == "09_ai_disclosure.md":
        body.append("\\clearpage\n")
    body.append("\\label{start:%d}%%\n" % i)
    body.append(convert(path))
    body.append("\n")
body.append("\\label{start:%d}%%\n" % len(ORDER))

tex = PREAMBLE + "\n".join(body) + "\n\\end{document}\n"
open(os.path.join(OUT, "whitepaper.tex"), "w").write(tex)
print("assembled", len(tex), "chars,", fig_counter[0], "figure placeholders")
