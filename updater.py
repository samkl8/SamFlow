"""
updater.py - houdt SamFlow bij via git.

De app draait uit een git-checkout (install.sh kloont de repo; de .app is een
launcher daarnaartoe), dus 'bijwerken' = een fast-forward pull + de app
herstarten. Geen aparte update-server nodig; GitHub is de bron.

Veilig by design:
- **Alleen fast-forward** (`merge --ff-only`), nooit mergen of forceren. Loopt de
  lokale checkout vooruit (eigen commits) of is de werkboom vuil, dan doet 'ie
  niets -- dat beschermt een ontwikkelmachine tegen zijn eigen werk.
- De pull raakt alleen bestanden op schijf. De draaiende Python houdt de oude
  code in het geheugen tot een herstart; daarom trekken we op de achtergrond
  binnen en passen we toe op een klik (of vanzelf bij de volgende login).

Alle functies zijn subprocess-only en veilig vanaf een achtergrondthread.
"""
import os
import shlex
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.expanduser("~/Applications/SamFlow.app")


def _git(*args, timeout=30):
    return subprocess.run(["git", "-C", BASE, *args],
                          capture_output=True, text=True, timeout=timeout)


def is_git_checkout():
    try:
        r = _git("rev-parse", "--is-inside-work-tree", timeout=5)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _branch():
    try:
        b = _git("rev-parse", "--abbrev-ref", "HEAD", timeout=5).stdout.strip()
        return b or "main"
    except Exception:
        return "main"


def _count(rangespec):
    try:
        return int((_git("rev-list", "--count", rangespec).stdout or "0").strip() or 0)
    except Exception:
        return 0


def check():
    """Haal op bij origin en meld de update-stand. Geeft een dict of None bij een
    fout / geen git. `can_apply` = True als een schone fast-forward kan."""
    if not is_git_checkout():
        return None
    try:
        if _git("fetch", "--quiet", "origin", timeout=45).returncode != 0:
            return None
        br = _branch()
        behind = _count(f"HEAD..origin/{br}")
        ahead = _count(f"origin/{br}..HEAD")
        dirty = bool(_git("status", "--porcelain").stdout.strip())
        subject = _git("log", "-1", "--format=%s", f"origin/{br}").stdout.strip()
        deps = "requirements.txt" in (_git("diff", "--name-only",
                                           f"HEAD..origin/{br}").stdout or "")
        return {
            "branch": br, "behind": behind, "ahead": ahead, "dirty": dirty,
            "subject": subject, "deps_changed": deps,
            "can_apply": behind > 0 and ahead == 0 and not dirty,
        }
    except Exception:
        return None


def apply(info):
    """Voer de fast-forward uit (en herinstalleer deps als requirements.txt
    wijzigde). Geeft (ok, bericht). Doet niets als can_apply False is."""
    if not info or not info.get("can_apply"):
        return False, "geen schone fast-forward mogelijk"
    br = info["branch"]
    try:
        m = _git("merge", "--ff-only", f"origin/{br}", timeout=60)
        if m.returncode != 0:
            return False, (m.stderr or "merge --ff-only faalde").strip()
        if info.get("deps_changed"):
            uv = _which_uv()
            if uv:
                subprocess.run(
                    [uv, "pip", "install",
                     "--python", os.path.join(BASE, ".venv", "bin", "python"),
                     "-r", os.path.join(BASE, "requirements.txt")],
                    capture_output=True, timeout=300)
        return True, "bijgewerkt"
    except Exception as e:
        return False, str(e)


def _which_uv():
    for p in (os.path.expanduser("~/.local/bin/uv"),
              "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if os.path.exists(p):
            return p
    try:
        return subprocess.run(["which", "uv"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def relaunch():
    """Herstart TCC-veilig: een los shell-proces wacht tot wij weg zijn en
    heropent de bundle (nooit `python samflow.py` los -- dat verliest de rechten,
    zie de TCC-val in README). De caller termineert de app hierna zelf."""
    subprocess.Popen(
        ["/bin/bash", "-c", f"sleep 1; open {shlex.quote(APP_PATH)}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def short_version():
    try:
        return _git("rev-parse", "--short", "HEAD", timeout=5).stdout.strip() or "?"
    except Exception:
        return "?"
