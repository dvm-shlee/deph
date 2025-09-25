"""
devh.zip
========

A set of lightweight utilities for **working with ZIP archives in-memory** and
providing convenient abstractions for files and directories inside a ZIP.

The focus is:
- Safe, Pythonic dataclasses (`FileBuffer`, `ZippedFile`, `ZippedDir`) wrapping
  raw bytes and `zipfile.ZipFile` entries.
- Support for extracting, isolating, and re-packing subtrees of a ZIP archive
  without touching the filesystem unless explicitly requested.
- Flexible `to_filename()` dispatcher to persist objects (`ZipFile`, `ZippedDir`,
  `ZippedFile`, `BytesIO`, or raw bytes) to disk in a normalized way.

Key abstractions
----------------
- **FileBuffer**  
  A simple wrapper around an in-memory `BytesIO` buffer.  
  Provides `.bytes()` to retrieve raw data and `.to_filename()` to persist
  directly to disk.

- **ZippedFile**  
  Represents a single file entry inside a ZIP archive.  
  Offers `.open()`, `.read()`, `.buffer()`, and `.isolate()` to access content,
  plus `.to_filename()` to save the file into a new one-file ZIP.

- **ZippedDir**  
  Represents a directory subtree inside a ZIP.  
  Provides `.isolate()` to generate a new ZIP containing only this subtree
  (optionally under a new root directory) and `.to_filename()` to persist it.

- **walk()**  
  Like `os.walk`, but operates over a `zipfile.ZipFile`.  
  Yields `(dirpath, dirnames, fileentries)` tuples, where fileentries are
  `ZippedFile` objects with direct access to contents.

- **fetch_files_in_zip() / fetch_dirs_in_zip()**  
  Helpers for searching within a ZIP by filename or directory name, supporting
  exact match, wildcards, or regex.

- **to_filename()**  
  A generic dispatcher to persist many kinds of in-memory objects to a ZIP file
  on disk. Handles `ZipFile`, `ZippedDir`, `ZippedFile`, `BytesIO`, and raw
  `bytes`.

Typical usage
-------------
```python
import zipfile
from devh import zip

# Load a zip from bytes
zf = zip.bytes_to_zipfile(zip_bytes)

# Walk the archive
for dirpath, dirnames, files in zip.walk(zf):
    for f in files:
        print(f.name, len(f.read()))

# Extract all "config.json" files
matches = zip.fetch_files_in_zip(zf, "config.json")
for m in matches:
    buf = m.isolate()   # -> FileBuffer
    buf.to_filename("/tmp/config.json")

# Isolate a subdirectory into a new in-memory zip
dirs = zip.fetch_dirs_in_zip(zf, "src")
if dirs:
    sub = dirs[0]  # ZippedDir
    new_zip = sub.isolate(add_root=True, root_name="package-src")  # -> zipfile.ZipFile (in-memory)
    with new_zip.open("package-src/module.py") as fh:
        print(fh.read().decode("utf-8"))

    # Optionally persist the isolated zip to disk:
    zip.to_filename(new_zip, "/tmp/package-src.zip")
```

Design notes
------------

- Uses only the stdlib (`zipfile`, `io`, `shutil`) for maximum portability.
- Preserves timestamps and file permissions (`external_attr`) where possible.
- Supports both in-memory workflows (`BytesIO`) and on-disk workflows
  (via `.to_filename()` or the `to_filename()` dispatcher).
- Explicit directory entries are preserved/added so that GUI ZIP browsers
  behave predictably.

Exports
-------

- FileBuffer
- ZippedFile
- ZippedDir
- walk
- bytes_to_zipfile
- fetch_files_in_zip
- fetch_dirs_in_zip
- to_filename
"""

import io
import os
import re
import zipfile
import fnmatch
import shutil
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, List, Tuple, Dict


@dataclass
class FileBuffer:
    """
    A simple in-memory file buffer object.
    """
    name: str
    buffer: io.BytesIO

    def bytes(self) -> bytes:
        """Return full bytes content."""
        pos = self.buffer.tell()
        try:
            self.buffer.seek(0)
            return self.buffer.read()
        finally:
            self.buffer.seek(pos)
            
    def to_filename(self, path: str | os.PathLike, *, overwrite: bool = True, makedirs: bool = True) -> str:
        """
        Write the buffer content to a file at `path`.

        Parameters
        ----------
        path : str | os.PathLike
            Destination file path.
        overwrite : bool, optional
            If False and the file exists, raise FileExistsError. Default True.
        makedirs : bool, optional
            If True, create parent directories as needed. Default True.

        Returns
        -------
        str
            The absolute filesystem path written to.
        """
        path = os.fspath(path)
        abs_path = os.path.abspath(path)

        if not overwrite and os.path.exists(abs_path):
            raise FileExistsError(f"File already exists: {abs_path}")

        parent = os.path.dirname(abs_path)
        if makedirs and parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        pos = self.buffer.tell()
        try:
            self.buffer.seek(0)
            with open(abs_path, "wb") as f:
                # use copyfileobj for efficient streaming of large buffers
                shutil.copyfileobj(self.buffer, f)
        finally:
            self.buffer.seek(pos)

        return abs_path
            

@dataclass
class ZippedFile:
    """
    A file-like handle to a file inside a ZipFile with convenient accessors.
    """
    name: str        # basename of the file (e.g., "README.md")
    arcname: str     # archive path inside the zip (e.g., "repo-123/README.md")
    zipobj: zipfile.ZipFile

    def open(self):
        """
        Return a readable file-like object (binary). Caller should close it.
        """
        return self.zipobj.open(self.arcname, "r")

    def read(self) -> bytes:
        """
        Read entire file content into bytes.
        """
        return self.zipobj.read(self.arcname)

    def buffer(self) -> io.BytesIO:
        """
        Return an in-memory BytesIO buffer holding the file content.
        """
        return io.BytesIO(self.read())
    
    def isolate(self) -> FileBuffer:
        """
        Return a FileBuffer that contains only this file's content.
        """
        buf = self.buffer()
        buf.seek(0)
        return FileBuffer(name=self.name, buffer=buf)


def create_from_dir(zip_path: str | os.PathLike, source_dir: str | os.PathLike,
                    compression: int = zipfile.ZIP_DEFLATED) -> str:
    """
    Create a ZIP archive from the contents of a directory.

    Parameters
    ----------
    zip_path : str | os.PathLike
        The path to the output ZIP file.
    source_dir : str | os.PathLike
        The path to the directory whose contents will be zipped.
    compression : int, optional
        The compression method to use (default: zipfile.ZIP_DEFLATED).

    Returns
    -------
    str
        The absolute path to the created ZIP file.
    """
    zip_path = os.fspath(zip_path)
    source_dir = os.fspath(source_dir)
    
    with zipfile.ZipFile(zip_path, 'w', compression=compression) as zf:
        for root, dirs, files in os.walk(source_dir):
            # Add directory entries
            for d in dirs:
                full_path = os.path.join(root, d)
                arcname = os.path.relpath(full_path, source_dir)
                zf.writestr(arcname + '/', b'') # Explicit directory entry
            # Add file entries
            for file in files:
                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, source_dir)
                zf.write(full_path, arcname)
    return os.path.abspath(zip_path)


@dataclass
class ZippedDir:
    """
    Directory-like node inside a ZipFile.
    Holds subdirectories and files.
    """
    name: str
    path: str
    dirs: List["ZippedDir"]
    files: List[ZippedFile]

    def as_dict(self):
        """Convert to plain dict (for debugging/serialization)."""
        return {
            "name": self.name,
            "path": self.path,
            "dirs": [d.as_dict() for d in self.dirs],
            "files": [f.name for f in self.files],
        }

    def _resolve_zipobj(self) -> zipfile.ZipFile:
        """
        Resolve the underlying ZipFile from any child file.
        Raise if not resolvable.
        """
        # Prefer a file's zipobj, else recurse into subdirs.
        stack = [self]
        while stack:
            node = stack.pop()
            for f in node.files:
                if f.zipobj:
                    return f.zipobj
            stack.extend(node.dirs)
        raise RuntimeError("Cannot resolve ZipFile for this ZippedDir (no files found).")
    
    def isolate(
        self,
        compression: int = zipfile.ZIP_DEFLATED,
        include_dir_entries: bool = True,
        add_root: bool = False,
        root_name: str | None = None,
    ) -> io.BytesIO:
        """
        Create a new ZIP (in-memory) that contains only this directory subtree.

        By default (add_root=False), the new ZIP's root is this directory itself,
        i.e., arcnames are relative to `self.path` (no extra top-level folder).

        If `add_root=True`, files are placed under a top-level directory named
        `root_name` (or `self.name` if `root_name` is None). In other words,
        entries will look like:  "<root_name>/<relative-path-inside-self>".

        Parameters
        ----------
        compression : int, optional
            Zip compression method (default: ZIP_DEFLATED).
        include_dir_entries : bool, optional
            If True, ensure folder entries (e.g., "a/", "a/b/") exist in the ZIP.
        add_root : bool, optional
            If True, wrap all contents under a top-level directory (default: False).
        root_name : str | None, optional
            Name of the top-level directory when `add_root=True`. If None, uses `self.name`.

        Returns
        -------
        io.BytesIO
            BytesIO containing the new ZIP archive. You may open it via:
                zipfile.ZipFile(io.BytesIO, "r")
        """
        src_zip = self._resolve_zipobj()

        # Normalize to POSIX style used inside zip archives
        prefix = self.path.strip("/")
        if prefix:
            prefix = prefix + "/"

        # Decide root folder name when requested
        if add_root:
            root = (root_name or (self.name or "root")).strip("/")
            root_prefix = f"{root}/"
        else:
            root = None
            root_prefix = ""

        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", compression=compression) as out_zip:
            # If wrapping with a root directory, add it explicitly (optional but nice for UIs)
            if add_root and include_dir_entries:
                ri = zipfile.ZipInfo(root_prefix)
                ri.external_attr = (0o40755 << 16)  # Unix dir mode
                out_zip.writestr(ri, b"")

            # Copy all entries whose filename starts with the directory prefix
            for info in src_zip.infolist():
                fn = info.filename
                if not fn.startswith(prefix):
                    continue

                # Derive arcname relative to this directory root
                rel = fn[len(prefix):]
                if not rel:  # skip the directory node itself (if present)
                    continue

                # Map to final arcname (with or without an added top-level root)
                if add_root:
                    arcname = root_prefix + rel
                else:
                    arcname = rel

                if arcname.endswith("/"):
                    # It's a directory entry in source zip
                    if include_dir_entries:
                        dir_info = zipfile.ZipInfo(arcname)
                        dir_info.date_time = info.date_time
                        dir_info.external_attr = (0o40755 << 16)
                        out_zip.writestr(dir_info, b"")
                    continue

                # It's a file: read from source and write to destination
                data = src_zip.read(info.filename)
                new_info = zipfile.ZipInfo(arcname)
                new_info.date_time = info.date_time
                new_info.external_attr = info.external_attr
                out_zip.writestr(new_info, data)

            if include_dir_entries:
                # Ensure intermediate directory entries exist for UIs expecting explicit dirs
                written = set(out_zip.namelist())
                need_dirs = set()
                for name in written:
                    if name.endswith("/"):
                        continue
                    parts = name.split("/")[:-1]
                    cur = []
                    for p in parts:
                        cur.append(p)
                        need_dirs.add("/".join(cur) + "/")

                for d in sorted(need_dirs):
                    if d not in written:
                        di = zipfile.ZipInfo(d)
                        di.external_attr = (0o40755 << 16)
                        out_zip.writestr(di, b"")

        out_buf.seek(0)
        return zipfile.ZipFile(out_buf, "r")

    
def walk(zipobj: zipfile.ZipFile, top: str = ""
    ) -> Iterable[Tuple[str, List[str], List[ZippedFile]]]:
    """
    Walk through a ZipFile like os.walk, but file entries provide direct access methods.

    Parameters
    ----------
    zipobj : zipfile.ZipFile
        Opened ZipFile object.
    top : str, optional
        Start directory inside the archive (default: root). Use archive paths, not OS paths.

    Yields
    ------
    (dirpath, dirnames, fileentries)
        dirpath : str
            Current archive path ('' for root or e.g., 'repo-abc/dir').
        dirnames : list[str]
            Sorted list of immediate subdirectory names.
        fileentries : list[ZippedFile]
            Sorted list of file entries; each has .open(), .read(), .buffer().
    """
    tree = defaultdict(lambda: {"dirs": set(), "files": {}})

    # Normalize and index
    for arcname in zipobj.namelist():
        # strip trailing '/' to unify directory entries
        norm = arcname.rstrip("/")
        parts = norm.split("/")
        parent = "/".join(parts[:-1])  # '' at root
        leaf = parts[-1]

        if arcname.endswith("/"):  # a directory entry
            tree[parent]["dirs"].add(leaf)
        else:  # a file entry
            tree[parent]["files"][leaf] = ZippedFile(
                name=leaf, arcname=norm, zipobj=zipobj
            )

        # ensure intermediate directories are known
        for i in range(len(parts) - 1):
            up_parent = "/".join(parts[:i])
            up_child = parts[i]
            tree[up_parent]["dirs"].add(up_child)

    # Depth-first traversal from `top`
    def _walk(cur: str):
        dirs = sorted(tree[cur]["dirs"])
        files = [tree[cur]["files"][k] for k in sorted(tree[cur]["files"].keys())]
        yield cur, dirs, files
        for d in dirs:
            sub = f"{cur}/{d}" if cur else d
            # only descend if path exists in the tree (zip may omit explicit dir entries)
            if sub in tree:
                yield from _walk(sub)

    # If top not present (e.g., user passed a prefix not explicitly indexed), still yield matching subtree
    start = top.rstrip("/")
    if start and start not in tree:
        # Build a filtered view for entries that start with this prefix
        pseudo = defaultdict(lambda: {"dirs": set(), "files": {}})
        for arcname in zipobj.namelist():
            if arcname.startswith(start + "/") or arcname.rstrip("/") == start:
                norm = arcname.rstrip("/")
                rel = norm[len(start):].lstrip("/")
                parent = "/".join([start] + ([p for p in rel.split("/")[:-1]] if rel else []))
                leaf = rel.split("/")[-1] if rel else start.split("/")[-1]
                if arcname.endswith("/"):
                    pseudo[parent]["dirs"].add(leaf)
                else:
                    pseudo[parent]["files"][leaf] = ZippedFile(leaf, norm, zipobj)
                # ensure intermediate
                prefix_parts = parent.split("/") if parent else []
                for i in range(len(prefix_parts)):
                    up_parent = "/".join(prefix_parts[:i])
                    up_child = prefix_parts[i]
                    pseudo[up_parent]["dirs"].add(up_child)

        if start not in pseudo:
            return  # nothing under this prefix
        tree = pseudo  # shadow for this branch only
        yield from _walk("")
    else:
        yield from _walk(start)


def bytes_to_zipfile(zip_bytes: bytes) -> zipfile.ZipFile:
    """
    Open a zip archive from a bytes object.

    This is a convenience wrapper around `zipfile.ZipFile(io.BytesIO(zip_bytes))`.

    Parameters
    ----------
    zip_bytes : bytes
        The binary content of a zip archive.

    Returns
    -------
    zipfile.ZipFile
        A readable ZipFile object.
    """
    return zipfile.ZipFile(io.BytesIO(zip_bytes))


def fetch_files_in_zip(
    zipobj: zipfile.ZipFile,
    filename: str,
    top: str = "",
    wildcard: bool = True,
    regex: str = None
) -> List["ZippedFile"]:
    """
    Search for files in a ZipFile whose leaf name matches `filename` (exact, wildcard, or regex).

    Parameters
    ----------
    zipobj : zipfile.ZipFile
        Opened ZipFile object.
    filename : str
        Target filename (exact match or pattern).
    top : str, optional
        Directory prefix to restrict search (default: root).
    wildcard : bool, optional
        If True, use fnmatch (shell-style wildcards) for filename matching.
    regex : str, optional
        If given, use this regex pattern to match filenames (overrides wildcard).

    Returns
    -------
    list[ZippedFile]
        List of matching ZippedFile objects.
    """
    matches = []
    pattern = re.compile(regex) if regex else None
    for _, _, fileentries in walk(zipobj, top=top):
        for entry in fileentries:
            if regex:
                if pattern.fullmatch(entry.name):
                    matches.append(entry)
            elif wildcard:
                if fnmatch.fnmatch(entry.name, filename):
                    matches.append(entry)
            else:
                if entry.name == filename:
                    matches.append(entry)
    return matches

def fetch_dirs_in_zip(
    zipobj: zipfile.ZipFile,
    dirname: str,
    top: str = "",
    wildcard: bool = True,
    regex: str | None = None,
    match_scope: str = "basename",   # "basename" | "fullpath"
) -> list[ZippedDir]:
    """
    Return ZippedDir trees rooted at the matched directories.

    Parameters
    ----------
    zipobj : zipfile.ZipFile
        The opened zip file object.
    dirname : str
        Directory name pattern to match.
    top : str, optional
        The starting directory inside the archive (default: root).
    wildcard : bool, optional
        Whether to allow wildcard matching (default: True).
    regex : str | None, optional
        Regex pattern to match directories (default: None).
    match_scope : {"basename", "fullpath"}, optional
        Matching scope:
        - "basename": match only against the final directory name.
        - "fullpath": match against the entire directory path.

    Returns
    -------
    list[ZippedDir]
        A list of matched ZippedDir objects.
    """
    index: Dict[str, Tuple[List[str], List[ZippedFile]]] = {}
    for dirpath, dirnames, fileentries in walk(zipobj, top=top):
        index[dirpath] = (dirnames, fileentries)

    def _target(dirpath: str) -> str:
        """Return the string (basename or fullpath) used for matching."""
        return dirpath.rsplit("/", 1)[-1] if match_scope == "basename" else dirpath

    def _match(dirpath: str) -> bool:
        """Check if the given directory path matches the provided criteria."""
        target = _target(dirpath)
        if regex is not None:
            return re.search(regex, target) is not None
        if wildcard:
            return fnmatch.fnmatch(target, dirname)
        return target == dirname

    def _build_dir(path: str) -> ZippedDir:
        """Recursively build the ZippedDir tree starting from the given path."""
        dirnames, files = index.get(path, ([], []))
        subdirs = []
        for d in dirnames:
            sub_path = f"{path}/{d}" if path else d
            subdirs.append(_build_dir(sub_path))
        return ZippedDir(
            name=path.rsplit("/", 1)[-1] if path else "",
            path=path,
            dirs=subdirs,
            files=files,
        )

    results: List[ZippedDir] = []
    for dirpath in index.keys():
        if _match(dirpath):
            results.append(_build_dir(dirpath))
    return results




def _copy_zip(zipobj: zipfile.ZipFile, dst_path: str,
              compression: int = zipfile.ZIP_DEFLATED,
              include_dir_entries: bool = True) -> None:
    """
    Copy all entries from an existing ZipFile to a new zip at dst_path.
    Re-compresses entries using `compression`.
    """
    with zipfile.ZipFile(dst_path, "w", compression=compression) as out:
        # Optionally ensure explicit dir entries first (helps some UIs)
        if include_dir_entries:
            dirs = set()
            for info in zipobj.infolist():
                name = info.filename
                if name.endswith("/"):
                    dirs.add(name)
                else:
                    parts = name.split("/")[:-1]
                    cur = []
                    for p in parts:
                        cur.append(p)
                        dirs.add("/".join(cur) + "/")
            for d in sorted(dirs):
                di = zipfile.ZipInfo(d)
                di.external_attr = (0o40755 << 16)
                out.writestr(di, b"")

        # Copy files and explicit dir entries preserving metadata where possible
        for info in zipobj.infolist():
            name = info.filename
            if name.endswith("/"):
                if include_dir_entries:
                    di = zipfile.ZipInfo(name)
                    di.date_time = info.date_time
                    di.external_attr = info.external_attr
                    out.writestr(di, b"")
                continue
            data = zipobj.read(name)
            ni = zipfile.ZipInfo(name)
            ni.date_time = info.date_time
            ni.external_attr = info.external_attr
            out.writestr(ni, data)


# --- ZippedFile method ---
def zippedfile_to_filename(self: ZippedFile, path: str, arcname: str | None = None,
                           compression: int = zipfile.ZIP_DEFLATED) -> str:
    """
    Save this single file into a new zip file at `path`.
    The resulting zip contains exactly one file (arcname or self.name).
    """
    arc = arcname or self.name
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        # ensure parent dirs inside zip
        parts = arc.split("/")[:-1]
        if parts:
            seen = set()
            cur = []
            for p in parts:
                cur.append(p)
                d = "/".join(cur) + "/"
                if d not in seen:
                    zi = zipfile.ZipInfo(d)
                    zi.external_attr = (0o40755 << 16)
                    zf.writestr(zi, b"")
                    seen.add(d)
        zf.writestr(arc, self.read())
    return path

# bind as method
ZippedFile.to_filename = zippedfile_to_filename


# --- ZippedDir method ---
def zippeddir_to_filename(self: ZippedDir, path: str,
                          compression: int = zipfile.ZIP_DEFLATED,
                          include_dir_entries: bool = True,
                          add_root: bool = False,
                          root_name: str | None = None) -> str:
    """
    Save this directory subtree into a new zip file at `path`.
    Mirrors ZippedDir.isolate() options; writes directly to disk.
    """
    src_zip = self._resolve_zipobj()
    prefix = self.path.strip("/")
    if prefix:
        prefix += "/"

    if add_root:
        root = (root_name or (self.name or "root")).strip("/")
        root_prefix = f"{root}/"
    else:
        root_prefix = ""

    with zipfile.ZipFile(path, "w", compression=compression) as out_zip:
        if add_root and include_dir_entries:
            ri = zipfile.ZipInfo(root_prefix)
            ri.external_attr = (0o40755 << 16)
            out_zip.writestr(ri, b"")

        # copy matching entries
        for info in src_zip.infolist():
            fn = info.filename
            if not fn.startswith(prefix):
                continue
            rel = fn[len(prefix):]
            if not rel:
                continue
            arcname = root_prefix + rel

            if arcname.endswith("/"):
                if include_dir_entries:
                    di = zipfile.ZipInfo(arcname)
                    di.date_time = info.date_time
                    di.external_attr = (0o40755 << 16)
                    out_zip.writestr(di, b"")
                continue

            data = src_zip.read(fn)
            ni = zipfile.ZipInfo(arcname)
            ni.date_time = info.date_time
            ni.external_attr = info.external_attr
            out_zip.writestr(ni, data)

        if include_dir_entries:
            written = set(out_zip.namelist())
            need_dirs = set()
            for name in written:
                if name.endswith("/"):
                    continue
                parts = name.split("/")[:-1]
                cur = []
                for p in parts:
                    cur.append(p)
                    need_dirs.add("/".join(cur) + "/")
            for d in sorted(need_dirs):
                if d not in written:
                    di = zipfile.ZipInfo(d)
                    di.external_attr = (0o40755 << 16)
                    out_zip.writestr(di, b"")
    return path

# bind as method
ZippedDir.to_filename = zippeddir_to_filename


# --- Generic dispatcher ---
def to_filename(obj,
                path: str,
                *,
                compression: int = zipfile.ZIP_DEFLATED,
                include_dir_entries: bool = True,
                add_root: bool = False,
                root_name: str | None = None,
                arcname: str | None = None) -> str:
    """
    Save `obj` to a zip file at `path`.

    Supported:
      - zipfile.ZipFile: copy all entries into a new zip.
      - ZippedDir: save the subtree (same options as ZippedDir.to_filename).
      - ZippedFile: save as a single-file zip (arcname optional).
      - str: encode as utf-8 and save as a single-file zip.
      - bytes/bytearray: save as a single-file zip.
      - io.BytesIO: save buffer content as a single-file zip.
    """
    if isinstance(obj, zipfile.ZipFile):
        _copy_zip(obj, path, compression=compression,
                  include_dir_entries=include_dir_entries)
        return path
    if isinstance(obj, ZippedDir):
        return obj.to_filename(path, compression=compression,
                               include_dir_entries=include_dir_entries,
                               add_root=add_root, root_name=root_name)
    if isinstance(obj, ZippedFile):
        return obj.to_filename(path, arcname=arcname, compression=compression)
    if isinstance(obj, (str, bytes, bytearray, io.BytesIO)):
        # Treat raw bytes/buffer as a single file to be zipped.
        # Use arcname if provided, otherwise derive from path.
        entry_name = arcname or os.path.basename(path).rsplit('.', 1)[0]
        if isinstance(obj, io.BytesIO):
            data = obj.getvalue()
        elif isinstance(obj, str):
            # Encode string to bytes, assuming utf-8
            data = obj.encode("utf-8")
        else:
            # obj is already bytes or bytearray
            data = obj
            
        with zipfile.ZipFile(path, "w", compression=compression) as zf:
            zf.writestr(entry_name, data)
        return path
    raise TypeError(f"Unsupported type for to_filename: {type(obj)!r}")


def write_bytesio_to_file(buf: io.BytesIO, path: str) -> None:
    """Write a BytesIO to a filesystem path."""
    pos = buf.tell()
    try:
        buf.seek(0)
        with open(path, "wb") as f:
            f.write(buf.read())
    finally:
        buf.seek(pos)

def load(path: str | os.PathLike) -> zipfile.ZipFile:
    """
    Opens a zip archive from a file path.

    A convenience wrapper for `zipfile.ZipFile(path, 'r')`.

    Parameters
    ----------
    path : str | os.PathLike
        Path to the zip archive file.

    Returns
    -------
    zipfile.ZipFile
        A readable ZipFile object.
    """
    return zipfile.ZipFile(os.fspath(path), 'r')

__all__ = ['FileBuffer', 
           'ZippedFile', 
           'ZippedDir',
           'walk', 
           'bytes_to_zipfile', 
           'create_from_dir',
           'load',
           'fetch_files_in_zip',
           'fetch_dirs_in_zip', 
           'to_filename',
           'write_bytesio_to_file']