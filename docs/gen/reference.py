"""Generates the reference pages of the book from their sources of truth.

    python docs/gen/reference.py <repo root> <book src dir>

Writes into <book src dir>/reference/:

- action.md        inputs and outputs of action.yml
- cli.md           help of the admin CLI, from its argparse parser
- python/<mod>.md  docstrings and signatures of every module in src/muelle

Needs PyYAML and Python 3.14 (the CLI is imported to read its parser).
"""

import argparse
import ast
import os
import re
import sys
import textwrap
from pathlib import Path

import yaml

REPO_URL = "https://github.com/haztecaso/muelle/blob/main"


def action_page(root: Path) -> str:
    action = yaml.safe_load((root / "action.yml").read_text())
    out = [
        "# Action inputs and outputs\n",
        f"Generated from [`action.yml`]({REPO_URL}/action.yml). See [Your first workflow]"
        "(../getting-started/first-workflow.md) for a complete workflow and "
        "[Payload and status API](payload.md) for what the action sends.\n",
        f"{action['description']}\n",
        "## Inputs\n",
        "| input | required | default | description |",
        "|---|---|---|---|",
    ]
    for name, spec in action["inputs"].items():
        required = "yes" if spec.get("required") else ""
        default = spec.get("default", "")
        default = f"`{default}`" if default != "" else ""
        out.append(f"| `{name}` | {required} | {default} | {_cell(spec['description'])} |")
    out += ["", "## Outputs\n", "| output | description |", "|---|---|"]
    for name, spec in action["outputs"].items():
        out.append(f"| `{name}` | {_cell(spec['description'])} |")
    out += ["", "## Environment of the client\n"]
    out.append(
        "The action runs [`muelle.client`](python/client.md), mapping each input to a `MUELLE_*` "
        "variable. The same client runs outside GitHub Actions as `muelle-client`:\n"
    )
    out += ["| variable | value |", "|---|---|"]
    for step in action["runs"]["steps"]:
        for var, value in step.get("env", {}).items():
            out.append(f"| `{var}` | `{_cell(value)}` |")
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def cli_page() -> str:
    # deterministic help: no colors, fixed width
    os.environ["COLUMNS"] = "80"
    os.environ["NO_COLOR"] = "1"
    from muelle import cli

    parser = cli._parser()
    out = [
        "# Admin CLI\n",
        f"Generated from the argparse parser in [`src/muelle/cli.py`]({REPO_URL}/src/muelle/cli.py). "
        "See [Operating the host](../guides/operating.md) for how to use it.\n",
        "```text",
        parser.format_help().rstrip(),
        "```\n",
    ]
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help for a in sub._choices_actions}
    for name, p in sub.choices.items():
        out += [f"## `muelle {name}`\n", f"{helps[name]}.\n", "```text", p.format_help().rstrip(), "```\n"]
    return "\n".join(out)


def python_page(path: Path, name: str) -> str:
    source = path.read_text()
    tree = ast.parse(source)
    out = [
        f"# `muelle.{name}`\n",
        f"Source: [`src/muelle/{path.name}`]({REPO_URL}/src/muelle/{path.name}).\n",
    ]
    if doc := ast.get_docstring(tree):
        out.append(_docstring(doc))
    lines = source.splitlines()
    for node in tree.body:
        out += _definition(node, lines, level=2)
    return "\n".join(out)


def _definition(node: ast.stmt, lines: list[str], level: int, prefix: str = "") -> list[str]:
    if not isinstance(node, ast.FunctionDef | ast.ClassDef) or node.name.startswith("_"):
        return []
    # the signature as written (ast.unparse would turn 0o644 into 420), without the colon
    signature = textwrap.dedent("\n".join(lines[node.lineno - 1 : max(node.body[0].lineno - 1, node.lineno)])).rstrip()
    out = [f"{'#' * level} `{prefix}{node.name}`\n", "```python", signature.removesuffix(":"), "```\n"]
    if doc := ast.get_docstring(node):
        out.append(_docstring(doc))
    if isinstance(node, ast.ClassDef):
        for child in node.body:
            out += _definition(child, lines, level + 1, f"{node.name}.")
    return out


def _docstring(doc: str) -> str:
    # docstrings are plain text: keep their line breaks and alignment
    return f"```text\n{doc}\n```\n"


def python_pages(root: Path, out: Path, summary: str) -> None:
    modules = sorted(p for p in (root / "src/muelle").glob("*.py") if p.name != "__init__.py")
    listed = set(re.findall(r"\(reference/python/(\w+)\.md\)", summary))
    found = {p.stem for p in modules}
    if listed != found:
        sys.exit(f"SUMMARY.md lists python modules {sorted(listed)}, src/muelle has {sorted(found)}")
    out.mkdir(parents=True, exist_ok=True)
    for path in modules:
        (out / f"{path.stem}.md").write_text(python_page(path, path.stem))


def main() -> None:
    root, src = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(root / "src"))
    reference = src / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    (reference / "action.md").write_text(action_page(root))
    (reference / "cli.md").write_text(cli_page())
    python_pages(root, reference / "python", (src / "SUMMARY.md").read_text())


if __name__ == "__main__":
    main()
