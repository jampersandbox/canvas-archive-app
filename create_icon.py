#!/usr/bin/env python3
"""
create_icon.py
==============
Generates icon.png, icon.ico (Windows) and icon.icns (Mac).
Run once:  python create_icon.py
Requires:  pip install pillow
"""
import os
import shutil
import subprocess
import sys
import tempfile

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing Pillow...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image, ImageDraw, ImageFont

SIZE = 512


def make_icon():
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = SIZE * 0.04
    draw.ellipse(
        [margin, margin, SIZE - margin, SIZE - margin],
        fill="#4a148c",
    )

    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Apple Color Emoji.ttc",
            int(SIZE * 0.55),
        )
    except Exception:
        try:
            font = ImageFont.truetype(
                "C:/Windows/Fonts/seguiemj.ttf", int(SIZE * 0.55)
            )
        except Exception:
            font = ImageFont.load_default()

    emoji = "\U0001F393"   # graduation cap — avoids encoding issues
    try:
        bbox = draw.textbbox((0, 0), emoji, font=font)
        tw   = bbox[2] - bbox[0]
        th   = bbox[3] - bbox[1]
        x    = (SIZE - tw) // 2 - bbox[0]
        y    = (SIZE - th) // 2 - bbox[1] - int(SIZE * 0.04)
        draw.text((x, y), emoji, font=font, embedded_color=True)
    except Exception:
        # Fallback: just draw a white circle if emoji fails
        draw.ellipse(
            [SIZE*0.3, SIZE*0.3, SIZE*0.7, SIZE*0.7],
            fill="white",
        )

    # Save PNG
    img.save("icon.png")
    print("OK: icon.png")

    # Save ICO (Windows)
    sizes    = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
    ico_imgs = [img.resize(s, Image.LANCZOS) for s in sizes]
    ico_imgs[0].save(
        "icon.ico", format="ICO", sizes=sizes,
        append_images=ico_imgs[1:],
    )
    print("OK: icon.ico")

    # Save ICNS (Mac)
    icns_sizes = {
        "icon_16x16.png":   16,
        "icon_32x32.png":   32,
        "icon_64x64.png":   64,
        "icon_128x128.png": 128,
        "icon_256x256.png": 256,
        "icon_512x512.png": 512,
    }

    if os.path.exists("/usr/bin/iconutil"):
        with tempfile.TemporaryDirectory(suffix=".iconset") as iconset:
            for fname, sz in icns_sizes.items():
                img.resize((sz, sz), Image.LANCZOS).save(
                    os.path.join(iconset, fname)
                )
                if sz <= 256:
                    img.resize((sz * 2, sz * 2), Image.LANCZOS).save(
                        os.path.join(
                            iconset,
                            fname.replace(".png", "@2x.png"),
                        )
                    )
            subprocess.run(
                ["iconutil", "-c", "icns", iconset, "-o", "icon.icns"],
                check=True,
            )
        print("OK: icon.icns")
    else:
        shutil.copy("icon.png", "icon.icns")
        print("OK: icon.icns (PNG fallback)")


if __name__ == "__main__":
    make_icon()
    print("All icons created!")