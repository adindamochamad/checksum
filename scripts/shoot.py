"""Screenshot the running app, waiting for the verification to actually finish.

Design is a quarter of this contest's score, and a page cannot be reviewed from an
HTTP status code. Headless Chrome's --screenshot fires under virtual time, which
does not wait for a streaming response, so it kept capturing the loading state.

    python scripts/shoot.py [out_dir] [--url URL]
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000"
QUESTION = "Which of our 2026 originals should we renew?"


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = pathlib.Path(args[0] if args else "shots")
    out.mkdir(parents=True, exist_ok=True)
    base = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--url=")), BASE)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                      device_scale_factor=2)

        await page.goto(base, wait_until="networkidle")
        await page.screenshot(path=out / "01-idle.png")
        print(f"  wrote {out/'01-idle.png'}")

        await page.fill("#question", QUESTION)
        await page.click("#run")

        # Mid-flight: readings landing one at a time is the moment worth showing.
        try:
            await page.wait_for_selector(".reading:nth-child(3)", timeout=90_000)
            await page.screenshot(path=out / "02-verifying.png")
            print(f"  wrote {out/'02-verifying.png'}")
        except Exception:
            print("  (no mid-flight frame -- replay was too fast)")

        await page.wait_for_selector("#receipts-section:not([hidden])", timeout=180_000)
        await page.wait_for_timeout(700)

        await page.screenshot(path=out / "03-full.png", full_page=True)
        print(f"  wrote {out/'03-full.png'}")

        stage = await page.query_selector("#stage")
        if stage:
            await stage.screenshot(path=out / "04-split.png")
            print(f"  wrote {out/'04-split.png'}")

        movement = await page.query_selector("#movement")
        if movement and not await movement.is_hidden():
            await movement.screenshot(path=out / "05-movement.png")
            print(f"  wrote {out/'05-movement.png'}")

        receipts = await page.query_selector(".receipt")
        if receipts:
            await receipts.screenshot(path=out / "06-receipt.png")
            print(f"  wrote {out/'06-receipt.png'}")

        for width, name in ((390, "07-mobile.png"), (820, "08-tablet.png")):
            await page.set_viewport_size({"width": width, "height": 900})
            await page.wait_for_timeout(400)
            await page.screenshot(path=out / name, full_page=True)
            print(f"  wrote {out/name}")

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
