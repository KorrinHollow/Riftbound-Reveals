name: Riftbound Watcher

on:
  schedule:
    - cron: "*/30 * * * *"
  workflow_dispatch:

# Prevents two runs from overlapping and racing each other's pushes
concurrency:
  group: riftbound-watcher
  cancel-in-progress: false

# Lets the job push gallery_state.json back to the repo
permissions:
  contents: write

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install playwright requests
          playwright install --with-deps chromium

      - name: Run watcher
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: python riftbound_gallery_watcher.py --once

      - name: Save seen cards
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@users.noreply.github.com"
          git add gallery_state.json
          if git diff --staged --quiet; then
            echo "No changes to save"
            exit 0
          fi
          git commit -m "Update seen cards"
          for i in 1 2 3; do
            if git pull --rebase -X theirs origin main && git push; then
              exit 0
            fi
            echo "Push failed, retrying ($i/3)..."
            sleep 3
          done
          exit 1
