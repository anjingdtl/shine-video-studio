"""Every shipped PowerShell script must parse on the Windows the package targets.

The package entry points run under Windows PowerShell 5.1 (`powershell.exe`),
including `START_APP.cmd`. A single PowerShell 7 construct - `?.`, `??`, a
ternary - makes such a script fail to *parse*, so the entry dies before printing
anything useful. `start_shine.ps1` shipped a `?.` and broke exactly that way, so
the whole directory is asserted here instead of by inspection.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 parser")

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"

HARNESS = r"""
$ErrorActionPreference = 'Stop'
$root = '{root}'
$failures = New-Object System.Collections.ArrayList
Get-ChildItem -LiteralPath $root -Filter *.ps1 -File | Sort-Object Name | ForEach-Object {{
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors -and $errors.Count -gt 0) {{
        [void]$failures.Add(('{{0}} L{{1}}: {{2}}' -f $_.Name, $errors[0].Extent.StartLineNumber, $errors[0].Message))
    }}
}}
if ($failures.Count -gt 0) {{
    $failures | ForEach-Object {{ Write-Output ('FAIL ' + $_) }}
    exit 1
}}
Write-Output ('PARSE_OK ' + (Get-ChildItem -LiteralPath $root -Filter *.ps1 -File).Count)
"""


def test_every_script_parses_under_windows_powershell_51(tmp_path):
    harness = tmp_path / "parse51.ps1"
    harness.write_text(HARNESS.format(root=str(SCRIPTS).replace("'", "''")), encoding="utf-8")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True, text=True, timeout=180,
        env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"},
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, output
    assert "PARSE_OK" in output, output


def test_no_powershell_7_only_operator_in_shipped_scripts():
    """A parse test catches it, but name the construct so the fix is obvious."""
    offenders = []
    for path in SCRIPTS.glob("*.ps1"):
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "?." in code or "??" in code:
                offenders.append(f"{path.name}:{number}: {line.strip()[:90]}")
    assert not offenders, "PowerShell 7 only syntax in scripts that 5.1 must parse:\n" + "\n".join(offenders)


def test_entry_wrappers_only_call_scripts_that_exist():
    packaging = REPO / "packaging" / "windows"
    for cmd in packaging.glob("*.cmd"):
        text = cmd.read_text(encoding="utf-8-sig")
        for match in re.findall(r"scripts\\(\w+)\.ps1", text):
            assert (SCRIPTS / f"{match}.ps1").is_file(), f"{cmd.name} calls missing scripts/{match}.ps1"
