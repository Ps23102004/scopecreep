"""ScopeCreep CLI — `check`, `serve`, `readme-sample`."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def check(
    pr: str = typer.Argument(..., help="Pull request to review, as owner/repo#123."),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    md: bool = typer.Option(False, "--md", help="Print a markdown report."),
    out: str = typer.Option(None, "--out", help="With --md, write the report here instead of stdout."),
    model: str = typer.Option(None, "--model", help="Override the judge model (default: chains.yaml cascade)."),
) -> None:
    """Check whether a PR's diff actually matches its stated title and issue."""
    from scopecreep.check import check as run_check
    from scopecreep.report import render_json, render_markdown, render_table

    try:
        result = run_check(pr, model=model)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(render_json(result))
        return
    if md:
        text = render_markdown(result)
        if out:
            Path(out).write_text(text + "\n", encoding="utf-8")
            typer.echo(f"wrote {out}")
        else:
            typer.echo(text)
        return
    typer.echo(render_table(result))


@app.command()
def serve() -> None:
    """Run the local dashboard on http://127.0.0.1:8200."""
    from scopecreep.server import main as server_main  # keeps `check` fast to start

    server_main()


@app.command("readme-sample")
def readme_sample(
    pr: str = typer.Option(None, "--pr", help="PR to sample (default: the pinned one)."),
    readme: str = typer.Option("README.md", "--readme", help="README to rewrite."),
) -> None:
    """Rerun `check` on a pinned PR and refresh README.md's example block."""
    from scopecreep.sample import refresh_readme

    path, source = refresh_readme(Path(readme), pr=pr)
    typer.echo(f"updated {path} from {source}")


if __name__ == "__main__":
    app()
