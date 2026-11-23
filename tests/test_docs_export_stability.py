"""Guard the generated eval, remediation, and fuzz sweep artifacts."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_exporters_are_byte_stable(tmp_path: Path) -> None:
    for exporter in ("evals_demo", "remediation_demo", "fuzz_sweep"):
        first, second = (tmp_path / exporter / run for run in ("first", "second"))
        for out, hash_seed in ((first, "1"), (second, "2")):
            out.mkdir(parents=True)
            if exporter == "evals_demo":
                command = [
                    "-c",
                    "import sys; from pathlib import Path; "
                    "from scripts.export_evals_demo import export; "
                    "export(demo=Path(sys.argv[1]))",
                    str(out),
                ]
            elif exporter == "remediation_demo":
                command = ["scripts/export_remediation_demo.py", "--out", str(out)]
            else:
                command = ["scripts/export_fuzz_sweep.py", "--output", str(out / "fuzz-sweep.json")]

            # Separate interpreters with different hash seeds expose unordered iteration.
            subprocess.run(
                [sys.executable, *command],
                cwd=ROOT,
                env={**os.environ, "PYTHONHASHSEED": hash_seed},
                check=True,
            )

        first_files = {path.relative_to(first) for path in first.rglob("*") if path.is_file()}
        second_files = {path.relative_to(second) for path in second.rglob("*") if path.is_file()}
        assert first_files, f"{exporter} produced no files"
        assert first_files == second_files, f"{exporter} produced different file sets"
        for path in sorted(first_files):
            assert (first / path).read_bytes() == (second / path).read_bytes(), (
                f"{exporter} produced different bytes for {path}"
            )
