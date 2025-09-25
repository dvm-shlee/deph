from __future__ import annotations
"""
devh.pip
========================

A tiny, safe wrapper around `pip` to run installs/uninstalls from Python
and get **structured results** (dicts) you can log or inspect. It embeds
cleanly in scripts/apps without touching pip internals.

Key ideas
---------
- Call `python -m pip ...` via `subprocess` (stable and venv-safe).
- Default to `--report -` for installs (pip >= 22) to emit a JSON report on stdout,
  which is returned as a Python `dict`.
- Provide helpers for GitHub (VCS) installs, bulk installs, version queries, and ensure logic.
- Avoid parsing human logs; rely on exit codes and JSON whenever possible.
- Target interpreter aware: pass a specific Python to the constructor, and all pip
  operations run under that interpreter. When `self.python == sys.executable`, metadata
  lookups are fast-pathed using `importlib.metadata`.

Limitations
-----------
- Requires pip >= 22 to produce JSON install reports; otherwise a minimal `{returncode, stdout, stderr}` dict is returned.
- Does **not** expose pip internals or dependency resolution beyond what `--report` provides.

Quick start
-----------
```python
from devh import pip

# Use current interpreter (default) or pin a specific one
installer = pip.Pip()  # or pip.Pip(python="/usr/bin/python3.11")

# 1) Regular install (returns pip JSON report dict when available)
rep = installer.install("requests", version="2.32.3")
print(rep.get("install", [{}])[0].get("metadata", {}).get("name"))

# 2) Install from GitHub (branch/tag/commit); supports PEP 508 with extras when package name is known
installer.install_github("pallets/flask", ref="3.0.x")
# installer.install_github("psf/requests", ref="main", package_name="requests", extras=["socks"])

# 3) Ensure a minimum version is present (installs or upgrades only if needed)
info = installer.ensure("numpy", min_version="2.0.0")
print(info)

# 4) List installed packages; optionally annotate with latest available version (fast, single extra call)
pkgs = installer.list_installed(get_latest=True)
for p in pkgs:
    print(p["name"], p["version"], p["latest"])

# 5) Check installation (target interpreter); returns version string or False
print(installer.is_installed("pydantic"))

# 6) Bulk install
reports = installer.install_many([
    {"module_name": "urllib3", "upgrade": True},
    {"module_name": "chardet", "version": "5.2.0"},
])

# 7) Query available versions from index
print(installer.available_versions("pandas")[:5])

# 8) Uninstall
installer.uninstall("some-package")

"""

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from typing import Any, Dict, List, Optional, Sequence
from packaging.version import Version, InvalidVersion


@dataclass
class RunResult:
    """Container for a pip subprocess result."""
    returncode: int
    stdout: str
    stderr: str


class Pip:
    """A small wrapper to run pip commands programmatically.

    - Uses `sys.executable -m pip` to respect the current interpreter/venv.
    - Prefers JSON outputs when available (e.g., `--report -`, `pip list --format=json`).
    - Raises `subprocess.CalledProcessError` on non-zero exit.
    """

    def __init__(self, python: Optional[str] = None) -> None:
        # If `python` is provided, use it; otherwise default to the current interpreter.
        self.python = python or sys.executable


    # ------------------------- Public API -------------------------
    def install_many(
        self,
        modules: List[Dict[str, Any]],
        *,
        report_on_stdout: bool = True,
        env: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Install multiple packages at once.

        Parameters
        ----------
        modules : List[Dict[str, Any]]
            Each dict should have at least {'module_name': str} and may include
            keys accepted by `install` (e.g., version, version_constraint, upgrade, no_deps, quiet, etc.).
        report_on_stdout : bool
            Pass through to install().
        env : Optional[dict]
            Environment variables to use.

        Returns
        -------
        List[Dict[str, Any]]
            List of reports, one per module in the same order.
        """
        results: List[Dict[str, Any]] = []
        for mod in modules:
            # Unpack options with defaults
            name = mod.get("module_name")
            if not name:
                raise ValueError("Each module spec must have 'module_name'")
            opts = {k: v for k, v in mod.items() if k != "module_name"}
            res = self.install(name, report_on_stdout=report_on_stdout, env=env, **opts)
            results.append(res)
        return results
    
    def install(
        self,
        module_name: str,
        *,
        version: Optional[str] = None,
        version_constraint: str = "==",
        upgrade: bool = False,
        no_deps: bool = False,
        quiet: bool = True,
        index_url: Optional[str] = None,
        extra_index_urls: Optional[Sequence[str]] = None,
        find_links: Optional[Sequence[str]] = None,
        report_on_stdout: bool = True,
        env: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Install a package via pip and return a structured report as dict.

        Parameters
        ----------
        module_name : str
            Package/distribution name, e.g., "numpy".
        version : Optional[str]
            Version string (e.g., "2.0.2"). If None, no constraint is applied.
        version_constraint : str
            One of "==", "!=", ">=", "<=", ">", "<", "~=", etc. Only used
            when `version` is provided.
        upgrade : bool
            Pass `--upgrade`.
        no_deps : bool
            Pass `--no-deps`.
        quiet : bool
            Pass `--quiet` to reduce noise.
        index_url : Optional[str]
            Custom `--index-url`.
        extra_index_urls : Optional[Sequence[str]]
            Additional `--extra-index-url` entries.
        find_links : Optional[Sequence[str]]
            Additional `--find-links` locations.
        report_on_stdout : bool
            If True (default), use `--report -` to capture JSON from stdout.
        env : Optional[dict]
            Environment vars to pass to subprocess.

        Returns
        -------
        Dict[str, Any]
            JSON report dict from pip (if available), else a minimal dict with
            keys: {"returncode", "stdout", "stderr"}.
        """
        spec = module_name if version is None else f"{module_name}{version_constraint}{version}"

        cmd: List[str] = [
            self.python, "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
        ]
        if upgrade:
            cmd.append("--upgrade")
        if no_deps:
            cmd.append("--no-deps")
        if quiet:
            cmd.append("--quiet")
        if index_url:
            cmd += ["--index-url", index_url]
        if extra_index_urls:
            for url in extra_index_urls:
                cmd += ["--extra-index-url", url]
        if find_links:
            for path in find_links:
                cmd += ["--find-links", path]

        if report_on_stdout and self._pip_supports_report():
            cmd += ["--report", "-"]  # JSON to stdout
        cmd.append(spec)

        result = self._run(cmd, env=env)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

        if self._is_json_report_output(cmd, result.stdout):
            return self._loads_json(result.stdout)
        # Fallback minimal object
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "note": "pip --report not available; returned raw output.",
        }

    def install_github(
        self,
        repo: str,
        *,
        ref: Optional[str] = None,
        subdir: Optional[str] = None,
        ssh: bool = False,
        package_name: Optional[str] = None,
        extras: Optional[Sequence[str]] = None,
        upgrade: bool = False,
        no_deps: bool = False,
        quiet: bool = True,
        report_on_stdout: bool = True,
        env: Optional[dict] = None,
        ) -> Dict[str, Any]:
        """Install a package directly from GitHub using pip's VCS URL syntax.


        Parameters
        ----------
        repo : str
        Either "owner/name" or a full Git/HTTPS URL. If not a URL, it will
        be expanded to a GitHub URL.
        ref : Optional[str]
        Branch/tag/commit (e.g., "main", "v1.2.3", "a1b2c3").
        subdir : Optional[str]
        Subdirectory in a monorepo containing `pyproject.toml` or setup.
        ssh : bool
        If True, use SSH form (git@github.com:...). Recommended for private repos.
        package_name : Optional[str]
        Distribution name as declared by the project (pyproject `name=`).
        Required if you want to attach extras per PEP 508.
        extras : Optional[Sequence[str]]
        Extras to install. Only applied when `package_name` is provided, per
        PEP 508 syntax: "<name>[extra1,extra2] @ <VCS-URL>". If `package_name`
        is omitted, extras are ignored.
        upgrade, no_deps, quiet, report_on_stdout, env : see `install()`.


        Returns
        -------
        Dict[str, Any]
        pip JSON report dict when available; otherwise a minimal dict with
        returncode/stdout/stderr.
        """
        # Build base VCS URL
        if repo.startswith(("git+https://", "git+ssh://", "https://", "ssh://", "git@")):
            base = repo if repo.startswith("git+") else ("git+" + repo)
        else:
            base = f"git+ssh://git@github.com/{repo}.git" if ssh else f"git+https://github.com/{repo}.git"


        at = f"@{ref}" if ref else ""
        frag = f"#subdirectory={subdir}" if subdir else ""
        vcs_url = f"{base}{at}{frag}"


        # Compose PEP 508 spec with optional package name and extras
        if package_name:
            extras_str = f"[{','.join(extras)}]" if extras else ""
            spec = f"{package_name}{extras_str} @ {vcs_url}"
        else:
            # Without a known distribution name, we cannot legally attach extras
            # per PEP 508. In that case we install by URL alone.
            spec = vcs_url

        cmd = [
            self.python, "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
        ]
        if upgrade:
            cmd.append("--upgrade")
        if no_deps:
            cmd.append("--no-deps")
        if quiet:
            cmd.append("--quiet")
        if report_on_stdout and self._pip_supports_report():
            cmd += ["--report", "-"]
        cmd.append(spec)

        result = self._run(cmd, env=env)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

        if self._is_json_report_output(cmd, result.stdout):
            return self._loads_json(result.stdout)
        return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "note": "pip --report not available; returned raw output.",
        }

    def uninstall(self, module_name: str, *, yes: bool = True, quiet: bool = True, env: Optional[dict] = None) -> Dict[str, Any]:
        """Uninstall a package via pip.

        Returns a minimal dict containing returncode/stdout/stderr.
        """
        cmd = [self.python, "-m", "pip", "uninstall", "--disable-pip-version-check", "--no-input"]
        if yes:
            cmd.append("-y")
        if quiet:
            cmd.append("--quiet")
        cmd.append(module_name)

        result = self._run(cmd, env=env)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    def list_installed(
        self,
        *,
        env: Optional[dict] = None,
        get_latest: bool = False,
        index_url: Optional[str] = None,
        extra_index_urls: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return installed packages via `pip list --format=json`.

        If `get_latest=True`, annotate each item with a `latest` field using a
        single extra call to `pip list --outdated --format=json` and merging
        results. This is significantly faster than querying each package with
        `pip index versions`.
        """
        # 1) Get the installed packages
        cmd = [self.python, "-m", "pip", "list", "--disable-pip-version-check", "--format", "json"]
        result = self._run(cmd, env=env)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
        try:
            packages: List[Dict[str, Any]] = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse pip list JSON: {e} Output head: {result.stdout[:200]!r}")

        if not get_latest:
            return packages

        # 2) Bulk-fetch latest versions for outdated packages
        outdated_cmd: List[str] = [
            self.python, "-m", "pip", "list", "--disable-pip-version-check", "--format", "json", "--outdated",
        ]
        if index_url:
            outdated_cmd += ["--index-url", index_url]
        if extra_index_urls:
            for url in extra_index_urls:
                outdated_cmd += ["--extra-index-url", url]

        outdated_res = self._run(outdated_cmd, env=env)

        # Build a name->latest_version map (gracefully handle failures/offline)
        latest_map: Dict[str, str] = {}
        if outdated_res.returncode == 0:
            try:
                for item in json.loads(outdated_res.stdout):
                    # pip >= 23 uses keys: name, version, latest_version, latest_filetype
                    name = item.get("name")
                    latest = item.get("latest_version") or item.get("latest")
                    if name and latest:
                        latest_map[name.lower()] = str(latest)
            except json.JSONDecodeError:
                pass

        # 3) Merge: if a package is not in latest_map, treat installed as latest
        for pkg in packages:
            name = (pkg.get("name") or "").lower()
            installed = pkg.get("version")
            pkg["latest"] = latest_map.get(name, installed)
        return packages

    def available_versions(
        self,
        module_name: str,
        *,
        include_prerelease: bool = False,
        index_url: Optional[str] = None,
        extra_index_urls: Optional[Sequence[str]] = None,
        env: Optional[dict] = None,
    ) -> List[str]:
        """Return available versions for a distribution from the configured index.

        Uses `pip index versions <name>` and parses the output. Optionally filters
        out pre-releases unless `include_prerelease` is True.
        """
        cmd: List[str] = [self.python, "-m", "pip", "index", "versions", module_name]
        cmd += ["--disable-pip-version-check"]
        if index_url:
            cmd += ["--index-url", index_url]
        if extra_index_urls:
            for url in extra_index_urls:
                cmd += ["--extra-index-url", url]

        res = self._run(cmd, env=env)
        if res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)

        versions: List[str] = []
        for line in res.stdout.splitlines():
            low = line.lower().strip()
            if low.startswith("available versions:"):
                _, right = line.split(":", 1)
                versions = [v.strip() for v in right.split(",") if v.strip()]
                break

        parsed = []
        for v in versions:
            try:
                parsed.append((Version(v), v))
            except InvalidVersion:
                parsed.append((Version("0"), v))  # Treat invalid versions as very old for sorting
        if not include_prerelease:
            parsed = [(pv, raw) for (pv, raw) in parsed if not pv.is_prerelease]
        parsed.sort(key=lambda t: t[0], reverse=True)  # latest first
        versions = [raw for (_, raw) in parsed]

        return versions

    def available_versions_many(
        self,
        names: Sequence[str],
        *,
        include_prerelease: bool = False,
        index_url: Optional[str] = None,
        extra_index_urls: Optional[Sequence[str]] = None,
        env: Optional[dict] = None,
    ) -> Dict[str, List[str]]:
        """Bulk version lookup. Calls `available_versions` for each name.

        Note: This issues one subprocess per package. For a large list,
        consider caching or limiting concurrency at a higher level.
        """
        out: Dict[str, List[str]] = {}
        for name in names:
            out[name] = self.available_versions(
                name,
                include_prerelease=include_prerelease,
                index_url=index_url,
                extra_index_urls=extra_index_urls,
                env=env,
            )
        return out

    def is_installed(self, module_name: str) -> str | bool:
        """
        Check installation in the *target* interpreter (self.python).
        Returns version string if installed, else False.
        Uses a fast local path when self.python == sys.executable.
        """
        ver = self._get_installed_version(module_name)
        return ver if ver is not None else False

    def ensure(self, 
        module_name: str, 
        *, 
        min_version: Optional[str] = None, 
        env: Optional[dict] = None
    ) -> Dict[str, Any]:
        """Ensure a package is installed (and meets `min_version` if provided).

        Returns
        -------
        Dict[str, Any]
            - If installation or upgrade is performed: the pip report/minimal dict from installing.
            - If no action is needed (already installed and meets requirements): a dict with keys:
                {
                    "status": "ok",
                    "message": str,
                    "installed_version": str,
                }
        """
        try:
            installed_ver = self._get_installed_version(module_name)
            if installed_ver is None:
                # Not installed -> install with >= constraint if min_version provided
                if min_version:
                    return self.install(module_name, version=min_version, version_constraint=">=", upgrade=False, env=env, quiet=False)
                return self.install(module_name, env=env, quiet=False)

            if min_version and version_lt(installed_ver, min_version):
                # Installed but too old -> upgrade to meet minimum
                return self.install(module_name, version=min_version, version_constraint=">=", upgrade=True, env=env, quiet=False)

        except subprocess.CalledProcessError as e:
            action = "installing"
            if "upgrade=True" in e.cmd:
                action = "upgrading"
            
            return {
                "status": "failed",
                "message": f"Failed while {action} '{module_name}'.",
                "stderr": e.stderr,
            }
        except Exception as e:
            return {
                "status": "failed",
                "message": f"An unexpected error occurred while trying to ensure '{module_name}': {type(e).__name__}",
                "stderr": str(e),
            }

        return {
            "status": "ok",
            "message": f"'{module_name}' already satisfies requirements (installed: {installed_ver})",
            "installed_version": installed_ver,
        }

    # ----------------------- Internal helpers -----------------------
    def _run(self, cmd: List[str], *, env: Optional[dict] = None) -> RunResult:
        # Use a clean text mode with universal newlines.
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._merged_env(env),
        )
        stdout = self._clean_ansi(proc.stdout)
        stderr = self._clean_ansi(proc.stderr)
        
        return RunResult(proc.returncode, stdout, stderr)

    def _merged_env(self, extra: Optional[dict]) -> Optional[dict]:
        if not extra:
            return None
        e = os.environ.copy()
        e.update(extra)
        return e

    def _pip_supports_report(self) -> bool:
        try:
            ver = self._get_installed_version("pip")
            if not ver:
                return False
            return Version(ver) >= Version("22")
        except Exception:
            return False

    def _is_json_report_output(self, cmd: List[str], stdout: str) -> bool:
        # When we pass `--report -`, stdout should be a JSON object.
        if " --report -" in (" ".join(shlex.quote(p) for p in cmd)):
            s = stdout.lstrip()
            return s.startswith("{") and s.rstrip().endswith("}")
        return False

    def _loads_json(self, data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:  # pragma: no cover
            raise RuntimeError(f"Failed to parse JSON report: {e}\nHead: {data[:200]!r}")

    def _get_installed_version(self, dist: str) -> Optional[str]:
        if self.python == sys.executable:
            try:
                return importlib_metadata.version(dist)
            except importlib_metadata.PackageNotFoundError:
                return None
            
        code = (
            "import json,sys; from importlib import metadata as m; d=sys.argv[1];\n"
            "try:\n"
            "  v=m.version(d); print(json.dumps({'ok':True,'ver':v}))\n"
            "except m.PackageNotFoundError:\n"
            "  print(json.dumps({'ok':False}))\n"
        )
        try:
            out = subprocess.check_output(
                [self.python, "-c", code, dist],
                text=True, stderr=subprocess.STDOUT, timeout=5
            )
            import json
            data = json.loads(out.strip())
            return data.get("ver") if data.get("ok") else None
        except Exception:
            return None
        
    def _clean_ansi(self, text: str) -> str:
        return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)

def version_lt(a: str, b: str) -> bool:
    try:
        return Version(a) < Version(b)
    except InvalidVersion:
        return False # Treat invalid versions as not comparable or older

__all__ = ["Pip", "RunResult", "version_lt"]