"""Download the OFL / Apache-licensed Google Fonts used for slogan rendering."""
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/google/fonts/main/"
FONTS = {
    "Bangers-Regular.ttf": "ofl/bangers/Bangers-Regular.ttf",
    "LuckiestGuy-Regular.ttf": "apache/luckiestguy/LuckiestGuy-Regular.ttf",
    "Anton-Regular.ttf": "ofl/anton/Anton-Regular.ttf",
    "Shrikhand-Regular.ttf": "ofl/shrikhand/Shrikhand-Regular.ttf",
    "Righteous-Regular.ttf": "ofl/righteous/Righteous-Regular.ttf",
    "Bungee-Regular.ttf": "ofl/bungee/Bungee-Regular.ttf",
    "BebasNeue-Regular.ttf": "ofl/bebasneue/BebasNeue-Regular.ttf",
    "ArchivoBlack-Regular.ttf": "ofl/archivoblack/ArchivoBlack-Regular.ttf",
    "Pacifico-Regular.ttf": "ofl/pacifico/Pacifico-Regular.ttf",
    "LilitaOne-Regular.ttf": "ofl/lilitaone/LilitaOne-Regular.ttf",
    "TitanOne-Regular.ttf": "ofl/titanone/TitanOne-Regular.ttf",
    "Chewy-Regular.ttf": "apache/chewy/Chewy-Regular.ttf",
    "Fredoka.ttf": "ofl/fredoka/Fredoka%5Bwdth,wght%5D.ttf",
    "Oswald.ttf": "ofl/oswald/Oswald%5Bwght%5D.ttf",
    "Monoton-Regular.ttf": "ofl/monoton/Monoton-Regular.ttf",
    "PermanentMarker-Regular.ttf": "apache/permanentmarker/PermanentMarker-Regular.ttf",
}


def main(dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    missing = 0
    for name, rel in FONTS.items():
        out = dest / name
        if out.exists() and out.stat().st_size > 1000:
            continue
        try:
            with urllib.request.urlopen(BASE + rel, timeout=30) as r:
                data = r.read()
            out.write_bytes(data)
            print(f"OK   {name} ({len(data) // 1024} KB)")
        except Exception as e:  # noqa: BLE001
            missing += 1
            print(f"MISS {name}: {e}")
    return 1 if missing else 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets" / "fonts"
    sys.exit(main(target))
