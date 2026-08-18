"""Install / uninstall the Quick Convert Explorer context-menu entry.

Usage:  python register.py install                  # Win11 "Show more options"
        python register.py install --classic-menu   # force top-level classic menu
        python register.py uninstall

Everything is written under HKEY_CURRENT_USER - no admin rights needed. By
default the entry appears in Windows 11's "Show more options" submenu and the
fast compact menu is left untouched. `--classic-menu` additionally forces the
old full menu so the entry sits at the top level, at the cost of slower
right-clicks system-wide. `uninstall` removes only what this script created,
including the classic-menu tweak if (and only if) this script added it.
"""

import sys
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "converter.py"
ICON = HERE / "quickconvert.ico"

MENU_KEYS = (
    r"Software\Classes\*\shell\QuickConvert",         # any file
    r"Software\Classes\Directory\shell\QuickConvert", # folders (compress)
)
# Empty default value on this CLSID makes Win11 show the full classic menu.
CLASSIC_MENU_CLSID = (r"Software\Classes\CLSID"
                      r"\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}")
CLASSIC_MENU_KEY = CLASSIC_MENU_CLSID + r"\InprocServer32"
# Records whether Quick Convert (not the user) created the classic-menu tweak,
# so uninstall only reverts a tweak we actually added.
OWNED_MARKER_KEY = MENU_KEYS[0]
OWNED_MARKER_NAME = "ClassicMenuCreatedByQuickConvert"


def _pythonw():
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw if pyw.exists() else sys.executable)


def _frozen():
    return getattr(sys, "frozen", False)


def _menu_command():
    """The command Explorer runs. Frozen: the exe itself; else pythonw+script."""
    if _frozen():
        return f'"{sys.executable}" "%1"'
    return f'"{_pythonw()}" "{SCRIPT}" "%1"'


def _set(path, default=None, **values):
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path)
    with key:
        if default is not None:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, default)
        for name, val in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)


def _key_exists(path):
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, path).Close()
        return True
    except FileNotFoundError:
        return False


def _delete_key(path):
    """Delete a single (leaf) key if present. Returns True if it was removed."""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        return True
    except FileNotFoundError:
        return False


def _delete_tree(path):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                             winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    with key:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(path + "\\" + sub)
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def make_icon():
    """Draw the swap-arrows icon so the menu entry has a face.

    The icon is purely cosmetic: any failure degrades to no icon rather than
    aborting the whole install.
    """
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([16, 16, 240, 240], radius=52, fill=(79, 70, 229))
        white = (255, 255, 255)
        d.rectangle([68, 90, 152, 110], fill=white)                  # top shaft
        d.polygon([(152, 76), (152, 124), (192, 100)], fill=white)   # top head
        d.rectangle([104, 146, 188, 166], fill=white)                # low shaft
        d.polygon([(104, 132), (104, 180), (64, 156)], fill=white)   # low head
        img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                              (64, 64), (128, 128), (256, 256)])
        return str(ICON)
    except Exception as e:
        print(f"(icon skipped: {e.__class__.__name__})")
        return None


def install(classic_menu=False):
    # Frozen exe carries its own embedded icon; the script build draws one.
    icon = sys.executable if _frozen() else make_icon()
    command = _menu_command()
    for base in MENU_KEYS:
        _set(base, MUIVerb="Quick Convert", MultiSelectModel="Player",
             **({"Icon": icon} if icon else {}))
        _set(base + r"\command", default=command)

    # By default Quick Convert appears under Windows 11's "Show more options"
    # (Shift+F10) and leaves the fast compact menu alone. Forcing the classic
    # full menu (so the entry sits at the top level) makes EVERY right-click
    # enumerate all legacy shell extensions, which is noticeably slower - so it
    # is opt-in only. We only claim ownership if the tweak isn't already set,
    # so uninstall never wipes a preference the user set themselves.
    if classic_menu and not _key_exists(CLASSIC_MENU_KEY):
        _set(CLASSIC_MENU_KEY, default="")
        _set(OWNED_MARKER_KEY, **{OWNED_MARKER_NAME: "1"})

    where = ("the classic full menu" if classic_menu
             else "Windows 11's \"Show more options\" submenu")
    print(f"Registered. Quick Convert will appear in {where}. "
          "Restart Explorer (or sign out/in) if you don't see it yet.")


def _step(label, fn):
    """Run one cleanup step; report but don't abort on failure."""
    try:
        fn()
    except Exception as e:
        print(f"  ! {label}: {e.__class__.__name__}: {e}")


def uninstall():
    # We created the classic-menu tweak only if the marker is present.
    owned = _key_exists(OWNED_MARKER_KEY) and _marker_present()

    for base in MENU_KEYS:
        _step(f"remove {base}", lambda base=base: _delete_tree(base))

    if owned:
        # Remove only the InprocServer32 leaf we added, then the parent CLSID
        # key if (and only if) it is now empty - never a user's other data.
        _step("remove classic-menu tweak", lambda: _delete_key(CLASSIC_MENU_KEY))
        if not _has_subkeys(CLASSIC_MENU_CLSID):
            _step("remove empty CLSID key",
                  lambda: _delete_key(CLASSIC_MENU_CLSID))
    else:
        print("  classic-menu tweak left in place (pre-existing or user-set).")

    _step("delete icon", lambda: ICON.exists() and ICON.unlink())
    print("Removed Quick Convert menu entries.")


def _marker_present():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, OWNED_MARKER_KEY) as k:
            winreg.QueryValueEx(k, OWNED_MARKER_NAME)
        return True
    except FileNotFoundError:
        return False


def _has_subkeys(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            winreg.EnumKey(k, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "install":
        install(classic_menu=("--classic-menu" in sys.argv))
    elif action == "uninstall":
        uninstall()
    else:
        print(__doc__)
        sys.exit(1)
