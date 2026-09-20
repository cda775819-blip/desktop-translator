# -*- coding: utf-8 -*-
"""Locate the app source and the model directory, wherever this repo lives.

The scripts in this repo were originally written against an absolute layout
(`D:\\translator-build\\app.py`, `D:\\Translator\\...`). That makes the repo
unusable anywhere else, so every script now goes through here.

Resolution order
----------------
app source  : $TRANSLATOR_APP, ./app.py, ../app.py, <this dir>/app.py
models      : $ARGOS_PACKAGES_DIR, <app_dir>/models/packages,
              %LOCALAPPDATA%/Translator/models/packages
"""
from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_app_source() -> str:
    """Absolute path to app.py."""
    candidates = [
        os.environ.get("TRANSLATOR_APP", ""),
        os.path.join(HERE, "app.py"),
        os.path.join(os.path.dirname(HERE), "app.py"),
        os.path.join(os.path.dirname(HERE), "Translator", "app.py"),
        os.path.join(HERE, "..", "..", "Translator", "app.py"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise FileNotFoundError(
        "找不到 app.py。设 TRANSLATOR_APP 环境变量，或把本目录放到 "
        "app.py 同级/上级。已尝试：\n  " + "\n  ".join(c for c in candidates if c)
    )


def _count_packages(path: str) -> int:
    try:
        return sum(1 for n in os.listdir(path)
                   if os.path.isdir(os.path.join(path, n)))
    except OSError:
        return 0


def find_model_dir(min_packages: int = 1) -> str | None:
    """Absolute path to models/packages, or None if not present.

    Searches upward from the app source too, so a repo checked out next to an
    installed copy finds that copy's models. Picks the candidate with the MOST
    packages rather than the first that exists: a stray near-empty models/
    directory next to the source (left over from a test run) would otherwise
    shadow the real collection.
    """
    candidates = [os.environ.get("ARGOS_PACKAGES_DIR", "")]
    app_dir = os.path.dirname(find_app_source_quiet())
    here = app_dir
    for _ in range(4):                      # app 所在目录及向上 3 层
        candidates.append(os.path.join(here, "models", "packages"))
        candidates.append(os.path.join(here, "Translator", "models", "packages"))
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    candidates.append(os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "Translator", "models", "packages"))

    best, best_n = None, 0
    for c in candidates:
        if not c or not os.path.isdir(c):
            continue
        n = _count_packages(c)
        if n >= min_packages and n > best_n:
            best, best_n = os.path.abspath(c), n
    return best


def find_app_source_quiet() -> str:
    try:
        return find_app_source()
    except FileNotFoundError:
        return HERE


def load_app(module_name: str = "translator_app"):
    """Import app.py as a module and return it.

    Sets ARGOS_PACKAGES_DIR first so the engine picks up the local models.
    """
    models = find_model_dir()
    if models:
        os.environ["ARGOS_PACKAGES_DIR"] = models
    path = find_app_source()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def find_dist_dir() -> str | None:
    """Where the built onedir output lives (contains the exe + _internal)."""
    for c in (os.environ.get("TRANSLATOR_DIST", ""),
              os.path.join(os.path.dirname(find_app_source_quiet()),
                           "Translator"),
              HERE):
        if c and os.path.isdir(os.path.join(c, "_internal")):
            return os.path.abspath(c)
    return None


def find_exe() -> str | None:
    dist = find_dist_dir()
    if not dist:
        return None
    for name in os.listdir(dist):
        if name.lower().endswith(".exe"):
            return os.path.join(dist, name)
    return None


if __name__ == "__main__":
    print("app source :", find_app_source())
    print("models     :", find_model_dir())
    print("dist       :", find_dist_dir())
    print("exe        :", find_exe())
