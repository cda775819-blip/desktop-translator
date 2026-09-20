# -*- coding: utf-8 -*-
"""Apply the lazy-import patches to a target site-packages.

Used for the build venv. The same two changes were applied by hand to the
user's Anaconda site-packages (with .bak backups).

Why: `import argostranslate.translate` otherwise executes
    ctranslate2.converters.__init__ -> transformers -> torch          (~34 s)
    argostranslate.sbd -> stanza -> spacy -> IPython, networkx        (~14 s)
Neither is reachable when translating with already-converted CTranslate2 models.
"""
import argparse
import os
import shutil
import sys


def patch_converters(sp: str) -> str:
    path = os.path.join(sp, "ctranslate2", "converters", "__init__.py")
    src = open(path, encoding="utf-8").read()
    if "LOCAL PATCH" in src:
        return f"already patched: {path}"
    old = "from ctranslate2.converters.transformers import TransformersConverter"
    if old not in src:
        raise SystemExit(f"unexpected content in {path}")
    new = (
        "# --- LOCAL PATCH (startup speed) ------------------------------------\n"
        "# Upstream imports TransformersConverter here, which unconditionally pulls\n"
        "# in `transformers` and therefore `torch`. Translating with an\n"
        "# already-converted CTranslate2 model never touches the converters.\n"
        "try:\n"
        "    from ctranslate2.converters.transformers import TransformersConverter\n"
        "except ImportError:  # transformers not installed: converters unavailable\n"
        "    TransformersConverter = None  # type: ignore[assignment]\n"
        "# --------------------------------------------------------------------\n"
    )
    shutil.copy2(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(src.replace(old, new))
    return f"patched: {path}"


def patch_sbd(sp: str) -> str:
    path = os.path.join(sp, "argostranslate", "sbd.py")
    src = open(path, encoding="utf-8").read()
    if "LOCAL PATCH" in src:
        return f"already patched: {path}"
    old = "import stanza\nfrom minisbd import SBDetect, models as minisbd_models\n"
    if old not in src:
        raise SystemExit(f"unexpected content in {path}")
    new = (
        "# --- LOCAL PATCH (startup speed) ------------------------------------\n"
        "# Upstream imports stanza eagerly; stanza pulls in spacy, IPython, networkx\n"
        "# and torch (~14s). It is only reachable when ARGOS_CHUNK_TYPE=STANZA.\n"
        "stanza = None\n"
        "\n"
        "\n"
        "def _lazy_stanza():\n"
        "    global stanza\n"
        "    if stanza is None:\n"
        "        import stanza as _stanza\n"
        "        stanza = _stanza\n"
        "    return stanza\n"
        "# --------------------------------------------------------------------\n"
        "\n"
        "from minisbd import SBDetect, models as minisbd_models\n"
    )
    src = src.replace(old, new)

    old_call = "            self.stanza_pipeline = stanza.Pipeline("
    if old_call in src:
        src = src.replace(old_call, "            self.stanza_pipeline = _lazy_stanza().Pipeline(")

    # The module-level spacy import is the remaining eager heavyweight: spacy
    # itself is cheap but `spacy.cli` pulls in torch (~7s). Only the SPACY chunk
    # type needs it, so skip the import with a safe fallback.
    old_spacy = (
        "try:\n"
        "    import spacy\n"
        "except ImportError:\n"
        "    spacy = None\n"
    )
    new_spacy = (
        "# LOCAL PATCH: was a bare `import spacy` (pulls spacy.cli -> torch).\n"
        "# spacy is only needed for ChunkType.SPACY, so failing over to None is the\n"
        "# same behaviour upstream already has for a missing spacy install.\n"
        "try:\n"
        "    import spacy\n"
        "except ImportError:  # pragma: no cover\n"
        "    spacy = None\n"
        "except Exception:  # pragma: no cover  (e.g. torch DLL load failure)\n"
        "    spacy = None\n"
    )
    if old_spacy in src:
        src = src.replace(old_spacy, new_spacy)

    # `spacy is not None` would be True for a half-imported module; guard the use.
    src = src.replace(
        "            self.nlp = spacy.load(pkg.packaged_sbd_path, exclude=[\"parser\"])",
        "            if spacy is None:\n"
        "                raise RuntimeError(\"SpaCy is not installed.\")\n"
        "            self.nlp = spacy.load(pkg.packaged_sbd_path, exclude=[\"parser\"])",
    )

    shutil.copy2(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(src)
    return f"patched: {path}"


def patch_sentencizer_choice(sp: str) -> str:
    """Make PackageTranslation prefer MiniSBD instead of the per-package stanza.

    Upstream logic is:
        if "stanza" in str(pkg.packaged_sbd_path):   Sentencizer = StanzaSentencizer
        elif "minisbd" in ...:                        Sentencizer = MiniSBDSentencizer
        else:                                         Sentencizer = MiniSBDSentencizer

    Every Argos package in this collection ships a `stanza/` subfolder, so the
    first branch always wins. That means every translation loads a stanza model
    through torch - the very thing we are trying to avoid - and without stanza
    installed it raises ModuleNotFoundError instead of translating.

    MiniSBD is the better choice here: it is ~0.2-1 MB per language, runs on
    onnxruntime, needs no torch, and its models are pre-seeded next to the app.
    So: prefer MiniSBD whenever it is available, and never let the stanza branch
    hard-fail the translation.
    """
    path = os.path.join(sp, "argostranslate", "translate.py")
    src = open(path, encoding="utf-8").read()
    if "LOCAL PATCH (sentencizer choice)" in src:
        return f"already patched: {path}"
    old = (
        '        if settings.chunk_type in [settings.ChunkType.ARGOSTRANSLATE, settings.ChunkType.DEFAULT]:\n'
        '            if "stanza" in str(pkg.packaged_sbd_path):\n'
        '                Sentencizer = StanzaSentencizer\n'
        '            elif "minisbd" in str(pkg.packaged_sbd_path):\n'
        '                Sentencizer = MiniSBDSentencizer\n'
        '            else:\n'
        '                Sentencizer = MiniSBDSentencizer # Default to MiniSBD if no stanza model is available\n'
    )
    if old not in src:
        raise SystemExit(f"unexpected sentencizer block in {path}")
    new = (
        '        # LOCAL PATCH (sentencizer choice): prefer MiniSBD.\n'
        '        # Note settings.chunk_type maps "DEFAULT" -> ARGOSTRANSLATE at import,\n'
        '        # so both values land in this single branch.\n'
        '        if settings.chunk_type in [settings.ChunkType.ARGOSTRANSLATE, settings.ChunkType.DEFAULT]:\n'
        '            if "minisbd" in str(pkg.packaged_sbd_path):\n'
        '                Sentencizer = MiniSBDSentencizer\n'
        '            elif "stanza" in str(pkg.packaged_sbd_path):\n'
        '                # Prefer MiniSBD: it needs no torch and its models ship with\n'
        '                # the app. Stanza is only used when MiniSBD data is absent.\n'
        '                Sentencizer = MiniSBDSentencizer\n'
        '            else:\n'
        '                Sentencizer = MiniSBDSentencizer\n'
    )
    src = src.replace(old, new)

    # If a stanza sentencizer is still selected but stanza is missing, degrade
    # to MiniSBD rather than failing every translation.
    old_ctor = (
        '        if Sentencizer is not None:\n'
        '            self.sentencizer = Sentencizer(pkg)\n'
    )
    new_ctor = (
        '        if Sentencizer is not None:\n'
        '            try:\n'
        '                self.sentencizer = Sentencizer(pkg)\n'
        '            except (ImportError, ModuleNotFoundError, RuntimeError):\n'
        '                self.sentencizer = MiniSBDSentencizer(pkg)\n'
    )
    if old_ctor in src:
        src = src.replace(old_ctor, new_ctor)

    shutil.copy2(path, path + ".bak")
    open(path, "w", encoding="utf-8").write(src)
    return f"patched: {path}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("site_packages")
    args = ap.parse_args()
    for fn in (patch_converters, patch_sbd, patch_sentencizer_choice):
        print(" ", fn(args.site_packages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
