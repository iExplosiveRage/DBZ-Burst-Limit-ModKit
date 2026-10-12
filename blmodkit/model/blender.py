"""Finding and running Blender headless for the export step (the input file is never saved)."""
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT_SCRIPT = os.path.join(HERE, "blender_export.py")


def find_blender(explicit=None):
    cands = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("BLENDER"):
        cands.append(os.environ["BLENDER"])
    w = shutil.which("blender")
    if w:
        cands.append(w)
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        if root:
            cands += sorted(glob.glob(os.path.join(root, "Blender Foundation", "Blender*", "blender.exe")),
                            reverse=True)
    cands += sorted(glob.glob("/Applications/Blender.app/Contents/MacOS/Blender")) + ["/usr/bin/blender"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("Blender not found: install Blender 4.x/5.x or pass --blender <path to blender.exe> "
                            "(or set BLENDER)")


def export(source, out_dir, blender=None, armature=None, include=None, exclude=None, include_hidden=False,
           keep_shape_keys=False, max_image=2048, use_pose=False, log=print):
    """Run the export script. source: .blend (opened with --disable-autoexec) or .fbx/.glb/.gltf/.dae
    (imported into an empty factory scene)."""
    exe = find_blender(blender)
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(source)[1].lower()
    if ext == ".blend":
        cmd = [exe, "-b", "--disable-autoexec", "--factory-startup", source]
    else:
        cmd = [exe, "-b", "--disable-autoexec", "--factory-startup"]
    cmd += ["--python", EXPORT_SCRIPT, "--", "--out", out_dir, "--max-image", str(int(max_image))]
    if ext != ".blend":
        cmd += ["--import", os.path.abspath(source)]
    if armature:
        cmd += ["--armature", armature]
    if include:
        cmd += ["--include", include]
    if exclude:
        cmd += ["--exclude", exclude]
    if include_hidden:
        cmd += ["--include-hidden"]
    if keep_shape_keys:
        cmd += ["--keep-shape-keys"]
    if use_pose:
        cmd += ["--use-pose"]
    log("  blender: %s" % exe)
    # CREATE_NO_WINDOW: no console window pops up when the kit runs without one (the GUI via pythonw)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                       errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with open(os.path.join(out_dir, "blender_log.txt"), "w", encoding="utf-8") as fh:
        fh.write(" ".join('"%s"' % c if " " in c else c for c in cmd) + "\n\n" + p.stdout)
    ok = [ln for ln in p.stdout.splitlines() if ln.startswith("EXPORT_OK")]
    if p.returncode != 0 or not ok:
        err = [ln for ln in p.stdout.splitlines() if "EXPORT_ERROR" in ln or "Error" in ln or "Traceback" in ln]
        raise RuntimeError("Blender export failed (see %s): %s" % (os.path.join(out_dir, "blender_log.txt"),
                                                                   " | ".join(err[-6:]) or p.stdout[-800:]))
    log("  " + ok[-1])
    return os.path.join(out_dir, "export.npz"), os.path.join(out_dir, "export.json")


if __name__ == "__main__":
    print(export(sys.argv[1], sys.argv[2]))
