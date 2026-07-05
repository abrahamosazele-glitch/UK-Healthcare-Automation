from pathlib import Path
from playwright.sync_api import sync_playwright

# Search page URL
URL = https://www.jobs.nhs.uk/candidate/jobadvert/E0358-26-0306?country=GB-ENG&searchFormType=main&keyword=Healthcare%20Assistant&location=London&language=en
print("DEBUG URL:", URL)
# Save location
OUTPUT_FILE = Path("tmp/nhs_rendered_job_detail.html")
OUTPUT_FILE.parent.mkdir(exist_ok=True)

with sync_playwright() as p:

    print("Launching browser...")

    browser = p.chromium.launch(headless=False)
    page = browser.new_page()


    print("Opening NHS Jobs...")
    page.goto(URL, wait_until="networkidle", timeout=60000)

    print(f"Title: {page.title()}")
    print(f"URL: {page.url}")

    html = page.content()

    OUTPUT_FILE.write_text(html, encoding="utf-8")

    print(f"Saved rendered HTML to: {OUTPUT_FILE}")
    print(f"HTML size: {len(html)} characters")

    input("Press Enter to close the browser...")
    browser.close()