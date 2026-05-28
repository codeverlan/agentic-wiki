from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent


def main() -> None:
    Image.new("RGB", (16, 9), "white").save(ROOT / "image.png")


if __name__ == "__main__":
    main()
